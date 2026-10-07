"""
Per-SELECT join analysis and alias helpers.

Why this module exists
----------------------
The old join-completeness check counted every table in the whole query as one
scope, and treated *any* equality between two qualified columns as a join.
That produced two kinds of mistakes on BIRD:
  * false alarms: tables inside an IN / = subquery were counted together with the
    outer table ("unjoined"), although each SELECT is self-contained;
  * misses: in a 3-table query with one ON clause removed, the remaining ON
    clauses still contained an equality, so the broken query passed.

Here every SELECT is analysed on its own: the FROM items of that SELECT are the
nodes of a small graph, every comparison in its ON / WHERE clauses that links two
FROM items is an edge, and the SELECT is "disconnected" when the physical tables
fall into more than one connected component.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from vasp_sql.core.schema import SchemaMetadata

_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def get_from(select: exp.Select) -> Optional[exp.From]:
    # sqlglot >= 26 stores FROM under "from_", older versions under "from"
    return select.args.get("from_") or select.args.get("from")


def from_items(select: exp.Select) -> List[exp.Expression]:
    """FROM items that belong directly to this SELECT (tables, derived tables), in order."""
    items: List[exp.Expression] = []
    frm = get_from(select)
    if frm is not None:
        if frm.this is not None:
            items.append(frm.this)
        items.extend(frm.expressions or [])
    for j in select.args.get("joins") or []:
        if j.this is not None:
            items.append(j.this)
    return items


def item_key(item: exp.Expression) -> str:
    """The name a FROM item is referenced by (alias if present, else table name)."""
    return (item.alias_or_name or "").lower()


def alias_to_table(select: exp.Select) -> Dict[str, str]:
    """qualifier (alias or table name, lowercase) -> physical table name (lowercase) for this SELECT."""
    out: Dict[str, str] = {}
    for it in from_items(select):
        if isinstance(it, exp.Table) and it.name:
            out[item_key(it)] = it.name.lower()
            out.setdefault(it.name.lower(), it.name.lower())
    return out


def table_to_qualifier(select: exp.Select) -> Dict[str, str]:
    """physical table name (lowercase) -> qualifier to use when referring to it in this SELECT."""
    out: Dict[str, str] = {}
    for it in from_items(select):
        if isinstance(it, exp.Table) and it.name:
            out.setdefault(it.name.lower(), it.alias_or_name)
    return out


def global_alias_map(ast: exp.Expression) -> Dict[str, str]:
    """alias -> table across the whole query (used when the enclosing SELECT is unknown)."""
    out: Dict[str, str] = {}
    for sel in ast.find_all(exp.Select):
        for k, v in alias_to_table(sel).items():
            out.setdefault(k, v)
    return out


def _walk_same_scope(node: exp.Expression):
    """Walk a predicate without descending into nested SELECTs / subqueries."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for child in n.iter_expressions():
            if isinstance(child, (exp.Select, exp.Subquery)):
                continue
            stack.append(child)


class _UnionFind:
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, k):
        while self.p[k] != k:
            self.p[k] = self.p[self.p[k]]
            k = self.p[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def select_components(select: exp.Select, schema: SchemaMetadata,
                      resolve_physical: bool = True) -> List[List[Tuple[str, Optional[str]]]]:
    """
    Connected components of the FROM items of one SELECT.
    Each component is a list of (qualifier, physical_table_or_None).
    With resolve_physical=False every table reference counts as physical (no schema lookup).
    """
    items = from_items(select)
    keys: List[str] = []
    phys: Dict[str, Optional[str]] = {}
    for it in items:
        k = item_key(it)
        if not k or k in phys:
            continue
        keys.append(k)
        is_tbl = isinstance(it, exp.Table) and bool(it.name)
        phys[k] = it.name.lower() if is_tbl and (not resolve_physical or schema.has_table(it.name)) else None
    if len(keys) <= 1:
        return [[(k, phys[k]) for k in keys]] if keys else []

    uf = _UnionFind(keys)

    def instances_of(expr: exp.Expression) -> set:
        found = set()
        for col in [n for n in _walk_same_scope(expr) if isinstance(n, exp.Column)]:
            q = (col.table or "").lower()
            if q:
                if q in phys:
                    found.add(q)
                continue
            # unqualified column: attribute it if exactly one physical FROM item has it
            owners = [k for k in keys if phys[k] and schema.get_column(phys[k], col.name)]
            if len(owners) == 1:
                found.add(owners[0])
        return found

    predicates: List[exp.Expression] = []
    seen_keys: List[str] = [item_key(items[0])] if items else []
    for j in select.args.get("joins") or []:
        jk = item_key(j.this) if j.this is not None else None
        if j.args.get("on") is not None:
            predicates.append(j.args["on"])
        if (j.args.get("using") or j.args.get("method") == "NATURAL" or
                str(j.args.get("kind") or "").upper() == "NATURAL") and jk in phys:
            for prev in seen_keys:
                if prev in phys:
                    uf.union(prev, jk)
        if jk:
            seen_keys.append(jk)
    where = select.args.get("where")
    if where is not None:
        predicates.append(where)

    for pred in predicates:
        for node in _walk_same_scope(pred):
            if isinstance(node, _COMPARISONS):
                linked = instances_of(node)
                linked = list(linked)
                for other in linked[1:]:
                    uf.union(linked[0], other)

    groups: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    for k in keys:
        groups.setdefault(uf.find(k), []).append((k, phys[k]))
    return list(groups.values())


def find_disconnected_selects(ast: exp.Expression, schema: SchemaMetadata):
    """
    Returns a list of (select_index, components) for every SELECT whose physical tables
    are split over 2+ components (i.e. an accidental Cartesian product).
    select_index is the position of the SELECT in list(ast.find_all(exp.Select)).
    """
    out = []
    for idx, sel in enumerate(ast.find_all(exp.Select)):
        comps = select_components(sel, schema)
        phys_comps = [c for c in comps if any(t for _, t in c)]
        if len(phys_comps) > 1:
            out.append((idx, phys_comps))
    return out


def defined_names(ast: exp.Expression) -> Tuple[set, set]:
    """
    Names the query defines itself (not schema objects):
      * output_names: column aliases (``COUNT(*) AS post_count``) and CTE column lists —
        an unqualified reference to one of these is valid (ORDER BY / HAVING / outer query);
      * source_names: aliases of derived tables and CTE names — columns qualified with
        one of these refer to query outputs, not schema columns.
    """
    output_names, source_names = set(), set()
    for a in ast.find_all(exp.Alias):
        if a.alias:
            output_names.add(a.alias.lower())
    for cte in ast.find_all(exp.CTE):
        if cte.alias:
            source_names.add(cte.alias.lower())
        cols = cte.args.get("alias")
        if isinstance(cols, exp.TableAlias):
            for c in cols.columns or []:
                output_names.add(c.name.lower())
    for sq in ast.find_all(exp.Subquery):
        if sq.alias:
            source_names.add(sq.alias.lower())
    return output_names, source_names


def is_query_defined_column(col_name: str, qualifier: Optional[str], names: Tuple[set, set]) -> bool:
    output_names, source_names = names
    if qualifier:
        return qualifier.lower() in source_names
    return col_name.lower() in output_names
