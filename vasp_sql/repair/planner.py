"""
Bộ lập kế hoạch sửa: lên các bản vá cục bộ trên AST và tạo các câu truy vấn ứng viên.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from vasp_sql.ast.parser import SQLParser
from vasp_sql.core.config import RepairBudget
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.diagnosis.engine import DiagnosticReport, FaultCategory
from vasp_sql.repair.patches import (
    ReplacePredicateValuePatch,
    AddJoinPatch,
    BaseASTPatch,
    ReplaceColumnPatch,
    ReplaceTablePatch,
)
from vasp_sql.schema_graph.graph import JoinPathStep, SchemaKnowledgeGraph


class CandidateQuery(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    repaired_sql: str = Field(description="SQL string resulting from applying the candidate patch.")
    repaired_ast: exp.Expression = Field(description="Patched AST tree.")
    applied_patches: List[BaseASTPatch] = Field(description="List of AST patch objects applied.")
    ast_edit_size: int = Field(default=0, description="Measured AST node difference from original.")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Estimated confidence score.")
    must_return_rows: bool = Field(
        default=False,
        description="Ứng viên sửa câu 'ra rỗng': chỉ chấp nhận nếu sau khi sửa câu trả về ít nhất 1 dòng."
    )
    agreement_group: Optional[int] = Field(
        default=None,
        description="Candidates sharing a group are alternative readings of the same fault "
                    "(e.g. two possible FK joins). One is accepted only if all valid ones return the same rows."
    )


class RepairPlanner:
    """
    Biến báo cáo chẩn đoán thành danh sách ứng viên đã xếp hạng, trong giới hạn RepairBudget.
    """

    def __init__(self, parser: Optional[SQLParser] = None):
        self.parser = parser or SQLParser()

    def plan_candidates(
        self,
        original_ast: exp.Expression,
        report: DiagnosticReport,
        schema: SchemaMetadata,
        kg: SchemaKnowledgeGraph,
        budget: Optional[RepairBudget] = None,
        dialect: str = "sqlite"
    ) -> List[CandidateQuery]:
        active_budget = budget or RepairBudget()
        candidates: List[CandidateQuery] = []

        if not report.has_faults:
            return []

        # Sắp xếp lỗi theo mức ưu tiên / độ tin cậy
        sorted_faults = sorted(report.faults, key=lambda f: f.confidence, reverse=True)

        # Chiến lược 4: thay giá trị lọc (NO_SUCH_VALUE) bằng giá trị thật trong database.
        # Mọi giá trị sai trong câu được sửa CÙNG LÚC trong một ứng viên (câu rỗng có thể do nhiều
        # giá trị cùng sai); nếu chỉ có một thì ứng viên đó cũng chính là bản sửa đơn.
        value_faults = [f for f in report.faults if f.fault_type == FaultCategory.NO_SUCH_VALUE]
        if value_faults:
            import json as _json
            patches = [
                ReplacePredicateValuePatch(
                    literal_index=f.literal_index,
                    old_literal_sql=f.target_element,
                    new_value=_json.loads(f.suggested_replacements[0]),
                    column=f"{f.table_context}.{f.column_name}",
                    reason=f.reason,
                    evidence=f.evidence,
                )
                for f in value_faults if f.suggested_replacements and f.literal_index is not None
            ]
            if patches:
                curr_ast = original_ast
                for p in patches:
                    curr_ast = p.apply(curr_ast)
                candidates.append(
                    CandidateQuery(
                        repaired_sql=self.parser.to_sql(curr_ast, dialect=dialect),
                        repaired_ast=curr_ast,
                        applied_patches=patches,
                        ast_edit_size=self.parser.calculate_ast_edit_size(original_ast, curr_ast),
                        confidence=round(min(f.confidence for f in value_faults) * 0.9, 2),
                        must_return_rows=True,
                    )
                )

        for fault in sorted_faults:
            if len(candidates) >= active_budget.max_candidates:
                break

            # Chiến lược 1: thay cột
            if fault.fault_type == FaultCategory.NO_SUCH_COLUMN:
                for suggestion in fault.suggested_replacements[:3]:
                    patch = ReplaceColumnPatch(
                        old_column=fault.target_element,
                        new_column=suggestion,
                        table_context=fault.table_context,
                        reason=fault.reason,
                        evidence=fault.evidence
                    )
                    try:
                        patched_ast = patch.apply(original_ast)
                        sql_str = self.parser.to_sql(patched_ast, dialect=dialect)
                        edit_size = self.parser.calculate_ast_edit_size(original_ast, patched_ast)

                        if edit_size <= active_budget.max_ast_edit_nodes:
                            candidates.append(
                                CandidateQuery(
                                    repaired_sql=sql_str,
                                    repaired_ast=patched_ast,
                                    applied_patches=[patch],
                                    ast_edit_size=edit_size,
                                    confidence=round(fault.confidence * 0.95, 2)
                                )
                            )
                    except Exception:
                        continue

            # Chiến lược 2: thay bảng
            elif fault.fault_type == FaultCategory.NO_SUCH_TABLE:
                for suggestion in fault.suggested_replacements[:2]:
                    patch = ReplaceTablePatch(
                        old_table=fault.target_element,
                        new_table=suggestion,
                        reason=fault.reason,
                        evidence=fault.evidence
                    )
                    try:
                        patched_ast = patch.apply(original_ast)
                        sql_str = self.parser.to_sql(patched_ast, dialect=dialect)
                        edit_size = self.parser.calculate_ast_edit_size(original_ast, patched_ast)

                        if edit_size <= active_budget.max_ast_edit_nodes:
                            candidates.append(
                                CandidateQuery(
                                    repaired_sql=sql_str,
                                    repaired_ast=patched_ast,
                                    applied_patches=[patch],
                                    ast_edit_size=edit_size,
                                    confidence=round(fault.confidence * 0.90, 2)
                                )
                            )
                    except Exception:
                        continue

            # Chiến lược 3: thêm JOIN — mỗi cách nối là một ứng viên
            elif fault.fault_type == FaultCategory.MISSING_JOIN:
                options = [[JoinPathStep(**st) for st in opt] for opt in fault.join_options]
                if len(options) > active_budget.max_candidates:
                    continue  # quá nhiều cách hiểu, không kiểm hết được trong ngân sách -> từ chối
                group = id(fault) if len(options) > 1 else None
                for opt in options:
                    if not opt:
                        continue
                    patches = [
                        AddJoinPatch(
                            step=step,
                            reason=fault.reason,
                            evidence=fault.evidence,
                            select_index=fault.scope_index or 0
                        )
                        for step in opt
                    ]
                    try:
                        curr_ast = original_ast
                        for p in patches:
                            curr_ast = p.apply(curr_ast)

                        sql_str = self.parser.to_sql(curr_ast, dialect=dialect)
                        edit_size = self.parser.calculate_ast_edit_size(original_ast, curr_ast)

                        if edit_size <= active_budget.max_ast_edit_nodes:
                            candidates.append(
                                CandidateQuery(
                                    repaired_sql=sql_str,
                                    repaired_ast=curr_ast,
                                    applied_patches=patches,
                                    ast_edit_size=edit_size,
                                    confidence=round(fault.confidence * 0.88, 2),
                                    agreement_group=group
                                )
                            )
                    except Exception:
                        continue

        return candidates[: active_budget.max_candidates]
