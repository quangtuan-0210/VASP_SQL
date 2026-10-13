"""
Phân tích phép nối theo từng SELECT và các hàm xử lý tên tắt.

Vì sao có module này
--------------------
Cách kiểm tra nối bảng cũ coi mọi bảng trong cả câu truy vấn là một scope, và coi
*bất kỳ* phép bằng nào giữa hai cột có tiền tố là một phép nối.
Điều đó gây hai loại sai trên BIRD:
  * báo nhầm: bảng trong truy vấn con IN / = bị tính chung với bảng ở ngoài
    ("chưa nối"), dù mỗi SELECT là độc lập;
  * bỏ sót: trong câu 3 bảng bị bỏ một mệnh đề ON, các mệnh đề ON còn lại vẫn
    có phép bằng, nên câu hỏng vẫn lọt qua.

Ở đây mỗi SELECT được phân tích riêng: các mục FROM của SELECT đó là các nút của
một đồ thị nhỏ, mỗi phép so sánh trong ON / WHERE nối hai mục FROM là một cạnh, và
SELECT bị coi là "tách rời" khi các bảng thật rơi vào nhiều hơn một thành phần liên thông.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from vasp_sql.core.schema import SchemaMetadata

_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def get_from(select: exp.Select) -> Optional[exp.From]:
    # sqlglot >= 26 lưu FROM dưới khoá "from_", bản cũ hơn dùng "from"
    return select.args.get("from_") or select.args.get("from")


def from_items(select: exp.Select) -> List[exp.Expression]:
    """Các mục FROM thuộc trực tiếp SELECT này (bảng, bảng con), theo thứ tự."""
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
    """Tên dùng để gọi một mục FROM (tên tắt nếu có, không thì tên bảng)."""
    return (item.alias_or_name or "").lower()


def alias_to_table(select: exp.Select) -> Dict[str, str]:
    """tiền tố (tên tắt hoặc tên bảng, chữ thường) -> tên bảng thật (chữ thường) trong SELECT này."""
    out: Dict[str, str] = {}
    for it in from_items(select):
        if isinstance(it, exp.Table) and it.name:
            out[item_key(it)] = it.name.lower()
            out.setdefault(it.name.lower(), it.name.lower())
    return out


def table_to_qualifier(select: exp.Select) -> Dict[str, str]:
    """tên bảng thật (chữ thường) -> tiền tố nên dùng khi nhắc tới nó trong SELECT này."""
    out: Dict[str, str] = {}
    for it in from_items(select):
        if isinstance(it, exp.Table) and it.name:
            out.setdefault(it.name.lower(), it.alias_or_name)
    return out


def global_alias_map(ast: exp.Expression) -> Dict[str, str]:
    """tên tắt -> bảng trên toàn câu truy vấn (dùng khi không biết SELECT bao quanh)."""
    out: Dict[str, str] = {}
    for sel in ast.find_all(exp.Select):
        for k, v in alias_to_table(sel).items():
            out.setdefault(k, v)
    return out


def _walk_same_scope(node: exp.Expression):
    """Duyệt một điều kiện mà không đi vào các SELECT / truy vấn con lồng bên trong."""
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
    Các thành phần liên thông của các mục FROM trong một SELECT.
    Mỗi thành phần là danh sách (tiền tố, bảng_thật_hoặc_None).
    Với resolve_physical=False, mọi tham chiếu bảng đều được coi là bảng thật (không tra schema).
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
            # cột không có tiền tố: gán cho bảng nếu đúng một bảng thật trong FROM có cột này
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
    Trả về danh sách (select_index, components) cho mọi SELECT có các bảng thật
    bị chia thành từ 2 thành phần trở lên (tức là vô tình tạo tích Descartes).
    select_index là vị trí của SELECT trong list(ast.find_all(exp.Select)).
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
    Các tên do chính câu truy vấn đặt ra (không phải đối tượng trong schema):
      * output_names: tên tắt của cột (``COUNT(*) AS post_count``) và danh sách cột của CTE —
        gọi tới các tên này mà không có tiền tố là hợp lệ (ORDER BY / HAVING / truy vấn ngoài);
      * source_names: tên tắt của bảng con và tên CTE — cột có tiền tố là một trong các tên này
        là đầu ra của truy vấn, không phải cột trong schema.
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
