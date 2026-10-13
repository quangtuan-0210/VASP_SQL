"""
Bộ chẩn đoán lỗi nhiều tầng cho câu SQL.
Gom bằng chứng từ cú pháp, schema, scope, phép nối và thông tin lỗi khi chạy.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlglot import exp

from vasp_sql.ast.joins import defined_names, find_disconnected_selects, is_query_defined_column
from vasp_sql.ast.parser import SQLParser
from vasp_sql.ast.scope import ScopeAnalyzer, ScopeContext
from vasp_sql.core.feedback import ExecutionFeedback
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.schema_graph import matching as name_match
from vasp_sql.schema_graph.graph import SchemaKnowledgeGraph


class FaultCategory(str, Enum):
    SYNTAX_ERROR = "SYNTAX_ERROR"
    NO_SUCH_TABLE = "NO_SUCH_TABLE"
    NO_SUCH_COLUMN = "NO_SUCH_COLUMN"
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"
    MISSING_JOIN = "MISSING_JOIN"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    NO_SUCH_VALUE = "NO_SUCH_VALUE"  # giá trị lọc không có trong dữ liệu (VD '40' trong khi dữ liệu ghi '0040')


class DiagnosedFault(BaseModel):
    fault_type: FaultCategory = Field(description="Category of the diagnosed fault.")
    target_element: str = Field(description="Name of the erroneous column, table, or AST node.")
    table_context: Optional[str] = Field(default=None, description="Associated table if known.")
    scope_index: Optional[int] = Field(
        default=None,
        description="Index of the SELECT (in ast.find_all(exp.Select) order) the fault belongs to, if known."
    )
    column_name: Optional[str] = Field(
        default=None,
        description="NO_SUCH_VALUE: tên cột (thật) đang bị lọc bằng giá trị không có trong dữ liệu."
    )
    literal_index: Optional[int] = Field(
        default=None,
        description="NO_SUCH_VALUE: vị trí của giá trị (literal) trong list(ast.find_all(exp.Literal))."
    )
    join_options: List[List[Dict[str, str]]] = Field(
        default_factory=list,
        description="MISSING_JOIN only: alternative join paths (each a list of FK steps). "
                    "More than one option means the schema alone cannot decide; candidates must agree on results."
    )
    reason: str = Field(description="Human-readable explanation of why this is considered an error.")
    evidence: str = Field(description="Grounding evidence from schema, graph, or sandbox.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in this diagnosis.")
    suggested_replacements: List[str] = Field(
        default_factory=list,
        description="Ranked candidate replacement names from Schema / Knowledge Graph."
    )


class DiagnosticReport(BaseModel):
    has_faults: bool = Field(default=False)
    faults: List[DiagnosedFault] = Field(default_factory=list)


class DiagnosisEngine:
    """
    Bộ chẩn đoán nhiều tầng, kiểm tra:
    1. Cú pháp hợp lệ (Gate A)
    2. Bảng có trong schema (Gate B)
    3. Phân giải scope và tên tắt (Gate B)
    4. Cột có trong schema và tìm cột gần giống (Gate B)
    5. Đủ điều kiện nối và đồ thị liên thông (Gate B)
    6. Thông tin lỗi khi chạy thử trong sandbox (Gate E)
    """

    def __init__(self, parser: Optional[SQLParser] = None, scope_analyzer: Optional[ScopeAnalyzer] = None):
        self.parser = parser or SQLParser()
        self.scope_analyzer = scope_analyzer or ScopeAnalyzer(self.parser)

    def diagnose(
        self,
        sql: str,
        schema: SchemaMetadata,
        kg: Optional[SchemaKnowledgeGraph] = None,
        execution_feedback: Optional[ExecutionFeedback] = None,
        dialect: str = "sqlite"
    ) -> DiagnosticReport:
        faults: List[DiagnosedFault] = []
        active_kg = kg or SchemaKnowledgeGraph(schema)

        # Tầng 1: kiểm tra cú pháp
        ast, parse_err = self.parser.try_parse(sql, dialect=dialect)
        if ast is None:
            faults.append(
                DiagnosedFault(
                    fault_type=FaultCategory.SYNTAX_ERROR,
                    target_element=sql,
                    reason=f"SQL syntax error: {parse_err}",
                    evidence=f"Parser failed for dialect {dialect}",
                    confidence=1.0
                )
            )
            return DiagnosticReport(has_faults=True, faults=faults)

        # Tầng 2: phân giải scope và bảng
        scope = self.scope_analyzer.analyze(ast, schema=schema, dialect=dialect)

        # Kiểm tra bảng có tồn tại trong schema không
        seen_bad_tables = set()
        for tbl_key, tbl_binding in scope.tables.items():
            if tbl_binding.is_cte or tbl_binding.is_subquery:
                continue

            phys_name = tbl_binding.table_name
            if not schema.has_table(phys_name) and phys_name.lower() not in seen_bad_tables:
                seen_bad_tables.add(phys_name.lower())
                # Tên bảng gần nhất trong schema; từ chối (không gợi ý) nếu có hai tên gần như nhau
                best, ambiguous, ranked = name_match.pick(
                    phys_name, [(t.name, t.name) for t in schema.tables.values()]
                )
                candidates = [best.item] if best else []
                note = (f"ambiguous between {[m.name for m in ranked[:2]]}" if ambiguous
                        else f"closest: {best.name} (edits={best.distance})" if best else "no close table name")
                faults.append(
                    DiagnosedFault(
                        fault_type=FaultCategory.NO_SUCH_TABLE,
                        target_element=phys_name,
                        reason=f"Table '{phys_name}' does not exist in schema.",
                        evidence=f"Name match {note}.",
                        confidence=1.0,
                        suggested_replacements=candidates
                    )
                )

        # Tầng 3: phân giải cột và tìm tên gần giống
        own_names = defined_names(ast)
        for col_binding in scope.column_bindings:
            col_name = col_binding.column_name
            target_table = col_binding.resolved_table

            # Bỏ qua dấu '*'
            if col_name == "*":
                continue
            # Bỏ qua tên do chính câu truy vấn đặt ra (tên tắt của SELECT, đầu ra của CTE / bảng con)
            if not target_table and is_query_defined_column(col_name, col_binding.qualifier, own_names):
                continue

            if target_table:
                # Đã biết bảng: kiểm tra cột có trong bảng không
                tbl_meta = schema.get_table(target_table)
                if tbl_meta and not tbl_meta.has_column(col_name):
                    # Bảng không có cột này -> lấy cột gần giống nhất của bảng đó.
                    # Nếu hai cột gần như nhau (VD A1 -> A10 / A11) thì không gợi ý -> TỪ CHỐI.
                    best, ambiguous, ranked = name_match.pick(
                        col_name, [(c.name, c.name) for c in tbl_meta.columns.values()]
                    )
                    suggested = [best.item] if best else []

                    faults.append(
                        DiagnosedFault(
                            fault_type=FaultCategory.NO_SUCH_COLUMN,
                            target_element=col_name,
                            table_context=target_table,
                            reason=f"Column '{col_name}' does not exist in table '{target_table}'.",
                            evidence=f"Table '{target_table}' columns: {list(tbl_meta.columns.keys())}",
                            confidence=0.95,
                            suggested_replacements=suggested
                        )
                    )
            else:
                # Không gán được cột cho bảng nào trong scope.
                # Nếu chính tên bảng bị sai thì có lẽ cột vẫn đúng -> sửa bảng trước.
                if seen_bad_tables:
                    continue
                # Ưu tiên cột của các bảng mà câu truy vấn đang dùng; không có thì tìm trên cả schema.
                in_query = sorted({
                    b.table_name for b in scope.tables.values()
                    if not b.is_cte and not b.is_subquery and schema.has_table(b.table_name)
                })
                pool = [((t, c.name), c.name) for t in in_query for c in schema.get_table(t).columns.values()]
                best, ambiguous, ranked = name_match.pick(col_name, pool)
                if best is None and not ambiguous:
                    pool = [((t.name, c.name), c.name) for t in schema.tables.values() for c in t.columns.values()]
                    best, ambiguous, ranked = name_match.pick(col_name, pool)
                suggested = [f"{best.item[0]}.{best.item[1]}"] if best else []
                faults.append(
                    DiagnosedFault(
                        fault_type=FaultCategory.NO_SUCH_COLUMN,
                        target_element=col_name,
                        reason=f"Column '{col_name}' cannot be resolved to any table in scope.",
                        evidence="Scope lacks table containing this column name.",
                        confidence=0.90,
                        suggested_replacements=suggested
                    )
                )

        # Tầng 4: kiểm tra đủ điều kiện nối bảng (theo từng SELECT, xem vasp_sql/ast/joins.py)
        for sel_idx, comps in find_disconnected_selects(ast, schema):
            # comps[0] chứa bảng gốc của FROM; nối mọi nhóm còn lại vào nó.
            # Khi schema có nhiều cách nối ngắn như nhau (VD hai khóa ngoại
            # giữa cùng hai bảng), mọi cách đều được giữ làm phương án: pipeline sẽ
            # chạy thử tất cả và chỉ chấp nhận khi chúng cho cùng kết quả.
            connected = {t for _, t in comps[0] if t}
            per_component = []  # danh sách các phương án, mỗi nhóm bị tách rời một danh sách
            for comp in comps[1:]:
                options = []
                for a in sorted(connected):
                    for b in sorted({t for _, t in comp if t}):
                        options.extend(active_kg.all_shortest_join_paths(a, b))
                if not options:
                    per_component = []
                    break  # không có đường khóa ngoại -> không có căn cứ để nối
                shortest = min(len(p) for p in options)
                uniq, seen = [], set()
                for p in options:
                    key = tuple((st.from_table, st.from_column, st.to_table, st.to_column) for st in p)
                    if len(p) == shortest and key not in seen:
                        seen.add(key)
                        uniq.append(p)
                per_component.append(uniq)
                connected |= {t for _, t in comp if t}
            if not per_component:
                continue

            combos = [[]]
            for opts in per_component:
                combos = [c + p for c in combos for p in opts][:6]
            all_tables = [t for c in comps for _, t in c if t]
            option_preds = [[st.to_sql_predicate() for st in c] for c in combos]
            faults.append(
                DiagnosedFault(
                    fault_type=FaultCategory.MISSING_JOIN,
                    target_element=",".join(all_tables),
                    scope_index=sel_idx,
                    reason=(f"Tables {all_tables} in the same SELECT are not linked by any join predicate (possible Cartesian product)."
                            + (f" {len(combos)} equally plausible join paths." if len(combos) > 1 else "")),
                    evidence=f"Knowledge Graph FK join options: {option_preds[:4]}",
                    confidence=0.85,
                    suggested_replacements=[" AND ".join(p) for p in option_preds],
                    join_options=[[st.model_dump() for st in c] for c in combos],
                )
            )

        # Tầng 5: dùng thông tin lỗi khi chạy thử trong sandbox
        if execution_feedback and not execution_feedback.is_success:
            err_type = execution_feedback.error_type
            err_msg = execution_feedback.error_message or ""

            # Kiểm tra lỗi khi chạy này đã được chẩn đoán chưa
            if not any(f.fault_type == FaultCategory.NO_SUCH_COLUMN for f in faults) and "no such column" in err_msg.lower():
                # Lấy tên cột từ thông báo lỗi (VD "no such column: schools.nam")
                col_part = err_msg.split(":")[-1].strip()
                faults.append(
                    DiagnosedFault(
                        fault_type=FaultCategory.NO_SUCH_COLUMN,
                        target_element=col_part,
                        reason=f"Sandbox execution failed: {err_msg}",
                        evidence="SQLite runtime error",
                        confidence=1.0,
                        suggested_replacements=[c[1] for c in active_kg.find_candidate_columns(col_part, top_k=3)]
                    )
                )
            elif not faults:
                faults.append(
                    DiagnosedFault(
                        fault_type=FaultCategory.RUNTIME_ERROR,
                        target_element=sql,
                        reason=f"Sandbox runtime execution failed: {err_msg}",
                        evidence=f"Error type: {err_type}",
                        confidence=0.80
                    )
                )

        return DiagnosticReport(has_faults=len(faults) > 0, faults=faults)
