"""
Localized AST Patches for VASP-SQL.
Transforms specific nodes in the AST while preserving all other query structures.
"""

from abc import ABC, abstractmethod
from typing import Optional
from sqlglot import exp

from vasp_sql.core.result import ASTPatchRecord
from vasp_sql.ast.joins import (
    alias_to_table,
    from_items,
    get_from,
    global_alias_map,
    item_key,
    select_components,
    table_to_qualifier,
)
from vasp_sql.schema_graph.graph import JoinPathStep


class BaseASTPatch(ABC):
    """
    Abstract base class for all localized AST patch operations.
    """

    def __init__(self, reason: str, evidence: str):
        self.reason = reason
        self.evidence = evidence

    @abstractmethod
    def apply(self, ast: exp.Expression) -> exp.Expression:
        """
        Apply the patch to a copy of the AST, returning the modified AST.
        """
        pass

    @abstractmethod
    def to_record(self) -> ASTPatchRecord:
        """
        Convert to structured ASTPatchRecord for reporting.
        """
        pass


class ReplaceColumnPatch(BaseASTPatch):
    """
    Replaces an invalid/misspelled column node in the AST with a validated column name.

    Alias-aware: the diagnosis reports the *physical* table (e.g. ``loan``) while the
    query usually writes an alias (``T2.accoount_id``). Qualifiers are resolved through
    the enclosing SELECT's FROM clause before comparing, and a ``table.column``
    suggestion is written back with the alias used in that SELECT.
    """

    def __init__(
        self,
        old_column: str,
        new_column: str,
        table_context: Optional[str] = None,
        reason: str = "Column does not exist in schema",
        evidence: str = "Matched closest valid column in schema"
    ):
        super().__init__(reason, evidence)
        self.old_column = old_column
        self.new_column = new_column
        self.table_context = table_context

    def apply(self, ast: exp.Expression) -> exp.Expression:
        cloned_ast = ast.copy()
        old_lower = self.old_column.lower()
        ctx_lower = self.table_context.lower() if self.table_context else None
        global_aliases = global_alias_map(cloned_ast)

        targets = []
        for col_node in cloned_ast.find_all(exp.Column):
            if col_node.name.lower() != old_lower:
                continue
            sel = col_node.find_ancestor(exp.Select)
            local = alias_to_table(sel) if sel is not None else {}
            if ctx_lower and col_node.table:
                q = col_node.table.lower()
                resolved = local.get(q) or global_aliases.get(q) or q
                if resolved != ctx_lower:
                    continue
            targets.append((col_node, sel))

        for col_node, sel in targets:
            quoted = bool(col_node.this.args.get("quoted")) if isinstance(col_node.this, exp.Identifier) else False
            if "." in self.new_column:
                tbl_part, col_part = self.new_column.split(".", 1)
                qual = table_to_qualifier(sel).get(tbl_part.lower(), tbl_part) if sel is not None else tbl_part
                col_node.set("this", exp.to_identifier(col_part, quoted=quoted or None))
                col_node.set("table", exp.to_identifier(qual))
            else:
                col_node.set("this", exp.to_identifier(self.new_column, quoted=quoted or None))

        return cloned_ast

    def to_record(self) -> ASTPatchRecord:
        return ASTPatchRecord(
            patch_type="ReplaceColumn",
            target_node_id=self.old_column,
            original_fragment=self.old_column,
            replacement_fragment=self.new_column,
            reason=self.reason,
            evidence=self.evidence
        )


class ReplaceTablePatch(BaseASTPatch):
    """
    Replaces an invalid table node in the AST with a validated table name.
    """

    def __init__(
        self,
        old_table: str,
        new_table: str,
        reason: str = "Table does not exist in schema",
        evidence: str = "Matched valid table name"
    ):
        super().__init__(reason, evidence)
        self.old_table = old_table
        self.new_table = new_table

    def apply(self, ast: exp.Expression) -> exp.Expression:
        cloned_ast = ast.copy()
        old_lower = self.old_table.lower()

        # Rename every reference to the wrong table; an alias ("FROM caards AS T1") is kept as is.
        for tbl_node in cloned_ast.find_all(exp.Table):
            if tbl_node.name.lower() == old_lower:
                quoted = bool(tbl_node.this.args.get("quoted")) if isinstance(tbl_node.this, exp.Identifier) else False
                tbl_node.set("this", exp.to_identifier(self.new_table, quoted=quoted or None))
        # Columns qualified with the wrong table name itself ("caards.id") follow the rename.
        for col_node in cloned_ast.find_all(exp.Column):
            if col_node.table and col_node.table.lower() == old_lower:
                col_node.set("table", exp.to_identifier(self.new_table))

        return cloned_ast

    def to_record(self) -> ASTPatchRecord:
        return ASTPatchRecord(
            patch_type="ReplaceTable",
            target_node_id=self.old_table,
            original_fragment=self.old_table,
            replacement_fragment=self.new_table,
            reason=self.reason,
            evidence=self.evidence
        )


class AddJoinPatch(BaseASTPatch):
    """
    Adds a missing join predicate using a validated JoinPathStep from the Knowledge Graph.

    Works on one SELECT (``select_index`` = position in ``ast.find_all(exp.Select)``) and is
    alias-aware: the ON clause uses the aliases already written in the query
    (``T1.driverId = T2.driverId``), and an existing table reference keeps its alias.

    Cases for the table being linked in (the step table that is not yet connected):
      * written as ``CROSS JOIN t`` / ``FROM a, t`` without predicate -> turned into
        ``INNER JOIN t ON <link>`` in place (moved to the end only if the link refers
        to a table declared later);
      * joined with an ON that does not link it -> ``ON <old> AND <link>``;
      * not in the query at all (intermediate table of a multi-hop path) -> appended.
    """

    def __init__(
        self,
        step: JoinPathStep,
        reason: str = "Missing JOIN predicate between related tables",
        evidence: str = "Foreign key relationship in Knowledge Graph",
        select_index: int = 0
    ):
        super().__init__(reason, evidence)
        self.step = step
        self.select_index = select_index
        self.joined_table = step.to_table  # updated in apply(); used by to_record()

    def apply(self, ast: exp.Expression) -> exp.Expression:
        cloned_ast = ast.copy()
        selects = list(cloned_ast.find_all(exp.Select))
        if not selects:
            return cloned_ast
        select_node = selects[self.select_index] if self.select_index < len(selects) else selects[0]

        from_tbl, to_tbl = self.step.from_table.lower(), self.step.to_table.lower()
        items = from_items(select_node)
        first_key = item_key(items[0]) if items else None

        # Which tables are already connected to the base FROM table?
        connected = set()
        comps = self.schema_free_components(select_node)
        for comp in comps:
            if first_key in {k for k, _ in comp}:
                connected = {t for _, t in comp if t}
        in_query = {it.name.lower() for it in items if isinstance(it, exp.Table) and it.name}

        if to_tbl in connected and from_tbl in connected:
            return cloned_ast  # nothing to do
        if to_tbl in connected or (from_tbl not in connected and from_tbl in in_query and to_tbl not in in_query):
            link_tbl, link_col, new_tbl, new_col = to_tbl, self.step.to_column, from_tbl, self.step.from_column
        else:
            link_tbl, link_col, new_tbl, new_col = from_tbl, self.step.from_column, to_tbl, self.step.to_column
        self.joined_table = new_tbl

        quals = table_to_qualifier(select_node)
        link_q = quals.get(link_tbl, link_tbl)

        # Locate the existing reference to new_tbl (prefer one that is not connected)
        frm = get_from(select_node)
        joins = list(select_node.args.get("joins") or [])
        target_join = None
        for j in joins:
            if isinstance(j.this, exp.Table) and j.this.name.lower() == new_tbl:
                target_join = j
                break
        comma_item = None
        if target_join is None and frm is not None:
            for e in frm.expressions or []:
                if isinstance(e, exp.Table) and e.name.lower() == new_tbl:
                    comma_item = e
                    break

        if target_join is not None:
            new_q = target_join.this.alias_or_name
        elif comma_item is not None:
            new_q = comma_item.alias_or_name
        else:
            new_q = new_tbl
        link_pred = exp.EQ(
            this=exp.column(link_col, table=link_q),
            expression=exp.column(new_col, table=new_q),
        )

        if target_join is not None:
            old_on = target_join.args.get("on")
            if old_on is not None:
                target_join.set("on", exp.and_(old_on, link_pred))
            else:
                target_join.set("on", link_pred)
                target_join.set("kind", "INNER")
                target_join.set("side", None)
                # if the link table is declared after this join, move the join to the end
                order = [item_key(it) for it in from_items(select_node)]
                if link_q.lower() in order and order.index(link_q.lower()) > order.index(new_q.lower()):
                    joins.remove(target_join)
                    joins.append(target_join)
                    select_node.set("joins", joins)
        elif comma_item is not None:
            frm.set("expressions", [e for e in frm.expressions if e is not comma_item])
            select_node.append("joins", exp.Join(this=comma_item.copy(), on=link_pred, kind="INNER"))
        else:
            select_node.append("joins", exp.Join(this=exp.to_table(new_tbl), on=link_pred, kind="INNER"))

        return cloned_ast

    @staticmethod
    def schema_free_components(select_node: exp.Select):
        """Connected FROM items using only the predicates written in the query (no schema needed)."""
        from vasp_sql.core.schema import SchemaMetadata
        return select_components(select_node, SchemaMetadata(db_id="_"), resolve_physical=False)

    def to_record(self) -> ASTPatchRecord:
        predicate = self.step.to_sql_predicate()
        return ASTPatchRecord(
            patch_type="AddJoin",
            target_node_id=self.joined_table,
            original_fragment="<missing join>",
            replacement_fragment=f"JOIN {self.joined_table} ON {predicate}",
            reason=self.reason,
            evidence=self.evidence
        )
