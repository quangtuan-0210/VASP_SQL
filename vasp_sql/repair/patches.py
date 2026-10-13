"""
Các bản vá cục bộ trên AST của VASP-SQL.
Chỉ đổi đúng những nút cần sửa trong AST, giữ nguyên mọi cấu trúc khác của câu truy vấn.
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
    Lớp cơ sở trừu tượng cho mọi thao tác vá cục bộ trên AST.
    """

    def __init__(self, reason: str, evidence: str):
        self.reason = reason
        self.evidence = evidence

    @abstractmethod
    def apply(self, ast: exp.Expression) -> exp.Expression:
        """
        Áp bản vá lên một bản sao của AST, trả về AST đã sửa.
        """
        pass

    @abstractmethod
    def to_record(self) -> ASTPatchRecord:
        """
        Chuyển thành ASTPatchRecord có cấu trúc để báo cáo.
        """
        pass


class ReplaceColumnPatch(BaseASTPatch):
    """
    Thay một nút cột sai / gõ sai trong AST bằng tên cột đã được kiểm chứng.

    Hiểu tên tắt: bước chẩn đoán báo bảng *thật* (VD ``loan``) trong khi câu truy vấn
    thường viết tên tắt (``T2.accoount_id``). Tiền tố được phân giải qua mệnh đề FROM của
    SELECT bao quanh trước khi so sánh, và gợi ý dạng ``bảng.cột`` được ghi lại bằng
    tên tắt mà SELECT đó đang dùng.
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
    Thay một nút bảng sai trong AST bằng tên bảng đã được kiểm chứng.
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

        # Đổi mọi chỗ tham chiếu tới bảng sai; tên tắt ("FROM caards AS T1") giữ nguyên.
        for tbl_node in cloned_ast.find_all(exp.Table):
            if tbl_node.name.lower() == old_lower:
                quoted = bool(tbl_node.this.args.get("quoted")) if isinstance(tbl_node.this, exp.Identifier) else False
                tbl_node.set("this", exp.to_identifier(self.new_table, quoted=quoted or None))
        # Cột có tiền tố là chính tên bảng sai ("caards.id") cũng được đổi theo.
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
    Thêm điều kiện nối còn thiếu, dùng một JoinPathStep đã kiểm chứng từ đồ thị tri thức.

    Làm việc trên một SELECT (``select_index`` = vị trí trong ``ast.find_all(exp.Select)``) và
    hiểu tên tắt: mệnh đề ON dùng các tên tắt đã có trong câu
    (``T1.driverId = T2.driverId``), và bảng đã có trong câu giữ nguyên tên tắt của nó.

    Các trường hợp của bảng cần nối vào (bảng trong bước nối chưa được nối):
      * viết dạng ``CROSS JOIN t`` / ``FROM a, t`` không có điều kiện -> đổi tại chỗ thành
        ``INNER JOIN t ON <điều kiện>`` (chỉ chuyển xuống cuối nếu điều kiện dùng tới
        bảng được khai báo sau);
      * đã JOIN với ON nhưng ON không nối tới nó -> ``ON <cũ> AND <điều kiện>``;
      * chưa có trong câu (bảng trung gian của đường nối nhiều chặng) -> thêm vào cuối.
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
        self.joined_table = step.to_table  # được cập nhật trong apply(); dùng bởi to_record()

    def apply(self, ast: exp.Expression) -> exp.Expression:
        cloned_ast = ast.copy()
        selects = list(cloned_ast.find_all(exp.Select))
        if not selects:
            return cloned_ast
        select_node = selects[self.select_index] if self.select_index < len(selects) else selects[0]

        from_tbl, to_tbl = self.step.from_table.lower(), self.step.to_table.lower()
        items = from_items(select_node)
        first_key = item_key(items[0]) if items else None

        # Những bảng nào đã nối với bảng gốc của FROM?
        connected = set()
        comps = self.schema_free_components(select_node)
        for comp in comps:
            if first_key in {k for k, _ in comp}:
                connected = {t for _, t in comp if t}
        in_query = {it.name.lower() for it in items if isinstance(it, exp.Table) and it.name}

        if to_tbl in connected and from_tbl in connected:
            return cloned_ast  # không cần làm gì
        if to_tbl in connected or (from_tbl not in connected and from_tbl in in_query and to_tbl not in in_query):
            link_tbl, link_col, new_tbl, new_col = to_tbl, self.step.to_column, from_tbl, self.step.from_column
        else:
            link_tbl, link_col, new_tbl, new_col = from_tbl, self.step.from_column, to_tbl, self.step.to_column
        self.joined_table = new_tbl

        quals = table_to_qualifier(select_node)
        link_q = quals.get(link_tbl, link_tbl)

        # Tìm chỗ đang tham chiếu tới new_tbl (ưu tiên chỗ chưa được nối)
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
                # nếu bảng dùng để nối được khai báo sau JOIN này thì chuyển JOIN xuống cuối
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
        """Các mục FROM liên thông, chỉ dựa trên các điều kiện viết trong câu (không cần schema)."""
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


class ReplacePredicateValuePatch(BaseASTPatch):
    """
    Thay một giá trị lọc (literal) bằng giá trị THẬT lấy từ database.

    VD:  WHERE CharterNum = '40'   ->   WHERE CharterNum = '0040'
    Chỉ đổi đúng một nút literal (xác định bằng vị trí trong list(ast.find_all(exp.Literal))),
    mọi phần khác của câu giữ nguyên. Kiểu của giá trị mới (chữ / số) theo đúng kiểu lưu trong database.
    """

    def __init__(
        self,
        literal_index: int,
        old_literal_sql: str,
        new_value,
        column: str = "",
        reason: str = "Value does not occur in the database column",
        evidence: str = "Closest value stored in the database",
    ):
        super().__init__(reason, evidence)
        self.literal_index = literal_index
        self.old_literal_sql = old_literal_sql
        self.new_value = new_value
        self.column = column

    def _new_literal(self) -> exp.Literal:
        if isinstance(self.new_value, (int, float)) and not isinstance(self.new_value, bool):
            return exp.Literal.number(self.new_value)
        return exp.Literal.string(str(self.new_value))

    def apply(self, ast: exp.Expression) -> exp.Expression:
        cloned_ast = ast.copy()
        literals = list(cloned_ast.find_all(exp.Literal))
        if self.literal_index < len(literals):
            node = literals[self.literal_index]
            # an toàn: chỉ thay khi đúng là literal cũ
            if node.sql(dialect="sqlite") == self.old_literal_sql:
                node.replace(self._new_literal())
        return cloned_ast

    def to_record(self) -> ASTPatchRecord:
        return ASTPatchRecord(
            patch_type="ReplacePredicateValue",
            target_node_id=self.column or None,
            original_fragment=self.old_literal_sql,
            replacement_fragment=self._new_literal().sql(dialect="sqlite"),
            reason=self.reason,
            evidence=self.evidence,
        )
