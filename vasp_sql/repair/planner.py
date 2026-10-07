"""
Repair Planner: Plans localized AST patches and generates candidate queries.
"""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from vasp_sql.ast.parser import SQLParser
from vasp_sql.core.config import RepairBudget
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.diagnosis.engine import DiagnosticReport, FaultCategory
from vasp_sql.repair.patches import (
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
    agreement_group: Optional[int] = Field(
        default=None,
        description="Candidates sharing a group are alternative readings of the same fault "
                    "(e.g. two possible FK joins). One is accepted only if all valid ones return the same rows."
    )


class RepairPlanner:
    """
    Translates diagnostic reports into a ranked pool of candidate queries subject to RepairBudget.
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

        # Sort faults by priority/confidence
        sorted_faults = sorted(report.faults, key=lambda f: f.confidence, reverse=True)

        for fault in sorted_faults:
            if len(candidates) >= active_budget.max_candidates:
                break

            # Strategy 1: Replace Column
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

            # Strategy 2: Replace Table
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

            # Strategy 3: Add Join — one candidate per alternative join path
            elif fault.fault_type == FaultCategory.MISSING_JOIN:
                options = [[JoinPathStep(**st) for st in opt] for opt in fault.join_options]
                if len(options) > active_budget.max_candidates:
                    continue  # too many readings to check them all within budget -> abstain
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
