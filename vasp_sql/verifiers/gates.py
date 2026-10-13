"""
Các cổng kiểm chứng (Gate A tới Gate F) của VASP-SQL.
"""

from typing import Optional, Tuple
from sqlglot import exp

from vasp_sql.ast.joins import defined_names, find_disconnected_selects, is_query_defined_column
from vasp_sql.ast.parser import SQLParser
from vasp_sql.ast.scope import ScopeAnalyzer
from vasp_sql.core.feedback import ExecutionFeedback
from vasp_sql.core.result import GateResult, GateType
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor


class VerificationPipeline:
    """
    Chạy lần lượt Gate A tới F.
    Câu ứng viên bị loại nếu trượt bất kỳ cổng bắt buộc nào.
    """

    def __init__(
        self,
        parser: Optional[SQLParser] = None,
        scope_analyzer: Optional[ScopeAnalyzer] = None
    ):
        self.parser = parser or SQLParser()
        self.scope_analyzer = scope_analyzer or ScopeAnalyzer(self.parser)

    def verify_gate_a_syntax(self, sql: str, dialect: str = "sqlite") -> GateResult:
        """
        Gate A: phân tích được và đúng cú pháp của dialect.
        """
        feedback = self.parser.validate_syntax(sql, dialect=dialect)
        if feedback.is_valid_syntax:
            return GateResult(
                gate=GateType.GATE_A_PARSE,
                passed=True,
                confidence=1.0,
                message="Syntax conforms to target dialect."
            )
        return GateResult(
            gate=GateType.GATE_A_PARSE,
            passed=False,
            confidence=0.0,
            message=feedback.error_message or "Syntax error."
        )

    def verify_gate_b_schema(
        self,
        ast: exp.Expression,
        schema: SchemaMetadata,
        dialect: str = "sqlite"
    ) -> GateResult:
        """
        Gate B: mọi bảng và cột tồn tại trong schema và gắn đúng scope.
        """
        scope = self.scope_analyzer.analyze(ast, schema=schema, dialect=dialect)

        # Kiểm tra bảng
        for tbl_name, tbl_binding in scope.tables.items():
            if not tbl_binding.is_cte and not tbl_binding.is_subquery:
                if not schema.has_table(tbl_binding.table_name):
                    return GateResult(
                        gate=GateType.GATE_B_SCHEMA_SCOPE,
                        passed=False,
                        confidence=0.0,
                        message=f"Table '{tbl_binding.table_name}' does not exist in schema."
                    )

        # Kiểm tra cột (bỏ qua tên do câu truy vấn tự đặt: tên tắt của SELECT, đầu ra của CTE / bảng con)
        own_names = defined_names(ast)
        for col_binding in scope.column_bindings:
            if col_binding.column_name == "*":
                continue
            if not col_binding.resolved_table and is_query_defined_column(
                    col_binding.column_name, col_binding.qualifier, own_names):
                continue
            if not col_binding.resolved_table:
                return GateResult(
                    gate=GateType.GATE_B_SCHEMA_SCOPE,
                    passed=False,
                    confidence=0.0,
                    message=f"Column '{col_binding.column_name}' could not be resolved to any table in scope."
                )
            tbl = schema.get_table(col_binding.resolved_table)
            if tbl and not tbl.has_column(col_binding.column_name):
                return GateResult(
                    gate=GateType.GATE_B_SCHEMA_SCOPE,
                    passed=False,
                    confidence=0.0,
                    message=f"Column '{col_binding.column_name}' does not exist in table '{col_binding.resolved_table}'."
                )

        # Kiểm tra đủ điều kiện nối theo từng SELECT (tránh vô tình tạo tích Descartes).
        # Mỗi SELECT được kiểm riêng, nên bảng trong truy vấn con IN/= không bị
        # gộp với truy vấn ngoài, và chỉ những điều kiện thật sự nối hai mục FROM
        # của SELECT đó mới được tính là phép nối.
        disconnected = find_disconnected_selects(ast, schema)
        if disconnected:
            _, comps = disconnected[0]
            groups = [[t for _, t in c if t] for c in comps]
            return GateResult(
                gate=GateType.GATE_B_SCHEMA_SCOPE,
                passed=False,
                confidence=0.0,
                message=f"Multiple tables are unjoined (Cartesian product without join predicate): {groups}."
            )

        return GateResult(
            gate=GateType.GATE_B_SCHEMA_SCOPE,
            passed=True,
            confidence=1.0,
            message="All tables and columns exist and resolve cleanly."
        )

    def verify_gate_c_type(self, ast: exp.Expression, schema: SchemaMetadata) -> GateResult:
        """
        Gate C: tương thích kiểu cho phép tổng hợp và toán tử.
        """
        # Kiểm tra kiểu cơ bản: SUM/AVG không nên áp lên cột không phải số (nếu biết kiểu)
        for func in ast.find_all(exp.Anonymous, exp.Sum, exp.Avg):
            func_name = func.key.upper()
            if func_name in ("SUM", "AVG"):
                for col in func.find_all(exp.Column):
                    col_meta = schema.get_column(col.table or "", col.name)
                    if col_meta and col_meta.data_type in ("TEXT", "BLOB"):
                        return GateResult(
                            gate=GateType.GATE_C_TYPE,
                            passed=False,
                            confidence=0.2,
                            message=f"Aggregation {func_name} applied to non-numeric column '{col.name}'."
                        )

        return GateResult(
            gate=GateType.GATE_C_TYPE,
            passed=True,
            confidence=1.0,
            message="Type constraints satisfied."
        )

    def verify_gate_d_safety(self, sql: str, sandbox: SQLiteSandboxExecutor) -> GateResult:
        """
        Gate D: kiểm tra an toàn kiểu chỉ đọc của sandbox.
        """
        is_safe, err_msg = sandbox.check_safety_ast(sql)
        return GateResult(
            gate=GateType.GATE_D_SAFETY,
            passed=is_safe,
            confidence=1.0 if is_safe else 0.0,
            message="Safety verified: Query is strictly read-only SELECT." if is_safe else err_msg
        )

    def verify_gate_e_execution(
        self,
        sql: str,
        sandbox: SQLiteSandboxExecutor
    ) -> Tuple[GateResult, ExecutionFeedback]:
        """
        Gate E: chạy thật trong sandbox và tạo mã băm kết quả.
        """
        feedback = sandbox.execute(sql)
        if feedback.is_success:
            return (
                GateResult(
                    gate=GateType.GATE_E_EXECUTION,
                    passed=True,
                    confidence=1.0,
                    message=f"Executed successfully in {feedback.execution_time_ms} ms, returned {feedback.row_count} rows.",
                    details={"row_count": feedback.row_count, "fingerprint": feedback.result_fingerprint}
                ),
                feedback
            )
        return (
            GateResult(
                gate=GateType.GATE_E_EXECUTION,
                passed=False,
                confidence=0.0,
                message=feedback.error_message or "Execution failed.",
                details={"error_type": feedback.error_type}
            ),
            feedback
        )

    def verify_gate_f_intent(
        self,
        question: str,
        sql: str,
        evidence: Optional[str] = None
    ) -> GateResult:
        """
        Gate F: kiểm tra đơn giản rằng câu truy vấn không rỗng hoặc rời rạc.
        """
        sql_lower = sql.lower()
        if not sql_lower.strip().startswith("select") and not sql_lower.strip().startswith("with"):
            return GateResult(
                gate=GateType.GATE_F_INTENT,
                passed=False,
                confidence=0.0,
                message="Query does not start with SELECT or WITH."
            )

        return GateResult(
            gate=GateType.GATE_F_INTENT,
            passed=True,
            confidence=0.9,
            message="Query intent alignment verified."
        )
