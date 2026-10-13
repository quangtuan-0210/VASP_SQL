"""
Phân tích AST có scope và phân giải tên.
Phân giải bảng, tên tắt, CTE, truy vấn con và liên kết cột.
"""

from typing import Any, Dict, List, Optional, Set, Union
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from vasp_sql.ast.parser import SQLParser
from vasp_sql.core.schema import SchemaMetadata


class ColumnBinding(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    column_name: str = Field(description="Unqualified column name.")
    qualifier: Optional[str] = Field(default=None, description="Table name or alias prefix as written in query.")
    resolved_table: Optional[str] = Field(default=None, description="Actual physical table name after resolving alias.")
    is_ambiguous: bool = Field(default=False, description="Whether column could belong to multiple tables in scope.")
    candidate_tables: List[str] = Field(default_factory=list, description="Tables in scope that contain this column.")
    ast_node: Optional[Any] = Field(default=None, description="Reference to sqlglot exp.Column node.")


class TableBinding(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    table_name: str = Field(description="Physical table name or CTE name.")
    alias: Optional[str] = Field(default=None, description="Alias assigned in the query if any.")
    is_cte: bool = Field(default=False, description="Whether this source is a Common Table Expression.")
    is_subquery: bool = Field(default=False, description="Whether this source is an inline derived subquery.")
    ast_node: Optional[Any] = Field(default=None, description="Reference to sqlglot Table or Subquery node.")


class ScopeContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    scope_id: int = Field(default=0, description="Sequential scope identifier.")
    parent_scope_id: Optional[int] = Field(default=None, description="Parent scope ID if nested.")
    
    # Ánh xạ từ tên tắt (hoặc tên bảng) sang TableBinding
    tables: Dict[str, TableBinding] = Field(default_factory=dict)
    
    # Ánh xạ tên các CTE định nghĩa trong scope này
    ctes: Dict[str, TableBinding] = Field(default_factory=dict)
    
    # Mọi liên kết cột đã phân giải trong scope này
    column_bindings: List[ColumnBinding] = Field(default_factory=list)
    
    # Danh sách các biểu thức JOIN
    joins: List[Any] = Field(default_factory=list)


class ScopeAnalyzer:
    """
    Bộ phân tích scope: duyệt AST của sqlglot và tạo ScopeContext đầy đủ.
    Phân giải liên kết cột dựa trên SchemaMetadata nếu có.
    """

    def __init__(self, parser: Optional[SQLParser] = None):
        self.parser = parser or SQLParser()

    def analyze(
        self,
        query: Union[str, exp.Expression],
        schema: Optional[SchemaMetadata] = None,
        dialect: str = "sqlite"
    ) -> ScopeContext:
        """
        Phân tích AST và tạo các liên kết trong scope.
        """
        if isinstance(query, str):
            ast = self.parser.parse(query, dialect=dialect)
        else:
            ast = query

        context = ScopeContext(scope_id=1)

        # 1. Lấy định nghĩa CTE (mệnh đề WITH)
        with_clause = ast.find(exp.With)
        if with_clause:
            for cte in with_clause.find_all(exp.CTE):
                cte_name = cte.alias_or_name.lower()
                cte_binding = TableBinding(
                    table_name=cte_name,
                    alias=cte_name,
                    is_cte=True,
                    ast_node=cte
                )
                context.ctes[cte_name] = cte_binding
                context.tables[cte_name] = cte_binding

        # 2. Lấy các bảng và truy vấn con từ FROM và JOIN
        for from_node in ast.find_all(exp.From):
            self._register_source(from_node.this, context)

        for join_node in ast.find_all(exp.Join):
            context.joins.append(join_node)
            self._register_source(join_node.this, context)

        # 3. Phân giải các cột
        for col_node in ast.find_all(exp.Column):
            binding = self._resolve_column(col_node, context, schema)
            context.column_bindings.append(binding)

        return context

    def _register_source(self, source_node: exp.Expression, context: ScopeContext) -> None:
        """
        Đăng ký một bảng, tham chiếu CTE hoặc truy vấn con vào ScopeContext.
        """
        if isinstance(source_node, exp.Table):
            tbl_name = source_node.name.lower()
            alias = source_node.alias.lower() if source_node.alias else None
            is_cte = tbl_name in context.ctes

            binding = TableBinding(
                table_name=tbl_name,
                alias=alias,
                is_cte=is_cte,
                is_subquery=False,
                ast_node=source_node
            )

            # Lưu cả tên bảng thật lẫn tên tắt để tra cứu nhanh
            context.tables[tbl_name] = binding
            if alias:
                context.tables[alias] = binding

        elif isinstance(source_node, (exp.Subquery, exp.Select)):
            alias = source_node.alias.lower() if source_node.alias else f"subquery_{len(context.tables)}"
            binding = TableBinding(
                table_name=alias,
                alias=alias,
                is_cte=False,
                is_subquery=True,
                ast_node=source_node
            )
            context.tables[alias] = binding

    def _resolve_column(
        self,
        col_node: exp.Column,
        context: ScopeContext,
        schema: Optional[SchemaMetadata]
    ) -> ColumnBinding:
        """
        Xác định cột này tham chiếu tới bảng thật nào.
        """
        col_name = col_node.name.lower()
        qualifier = col_node.table.lower() if col_node.table else None

        resolved_table = None
        is_ambiguous = False
        candidates: List[str] = []

        if qualifier:
            # Trường hợp 1: cột có tiền tố rõ ràng (VD s.name hoặc schools.name)
            if qualifier in context.tables:
                resolved_table = context.tables[qualifier].table_name
                candidates = [resolved_table]
            else:
                # Tiền tố / tên tắt không nhận ra
                resolved_table = None
                candidates = []
        else:
            # Trường hợp 2: cột không có tiền tố (VD name)
            if schema:
                # Tìm mọi bảng trong scope hiện tại thật sự có cột này trong schema
                for tbl_binding in context.tables.values():
                    phys_tbl = tbl_binding.table_name
                    if schema.has_table(phys_tbl):
                        tbl_meta = schema.get_table(phys_tbl)
                        if tbl_meta and tbl_meta.has_column(col_name):
                            if phys_tbl not in candidates:
                                candidates.append(phys_tbl)

                if len(candidates) == 1:
                    resolved_table = candidates[0]
                elif len(candidates) > 1:
                    is_ambiguous = True
                    resolved_table = candidates[0]  # mặc định lấy ứng viên đầu tiên
            else:
                # Nếu không có schema và scope chỉ có 1 bảng thì gán cho bảng đó
                unique_tables = list({b.table_name for b in context.tables.values() if not b.is_subquery})
                if len(unique_tables) == 1:
                    resolved_table = unique_tables[0]
                    candidates = [resolved_table]

        return ColumnBinding(
            column_name=col_node.name,
            qualifier=col_node.table if col_node.table else None,
            resolved_table=resolved_table,
            is_ambiguous=is_ambiguous,
            candidate_tables=candidates,
            ast_node=col_node
        )
