"""
Repair Policy definition for VASP-SQL.
"""

from typing import List
from pydantic import BaseModel, Field

from vasp_sql.core.config import ExecutionSafetyPolicy, RepairBudget
from vasp_sql.core.result import GateType


class RepairPolicy(BaseModel):
    """
    Policy governing the repair process, confidence thresholds, and active gates.
    """
    min_confidence_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum aggregated confidence required to accept a patch instead of ABSTAIN."
    )
    budget: RepairBudget = Field(
        default_factory=RepairBudget,
        description="Budget limits (candidates, LLM calls, time)."
    )
    safety: ExecutionSafetyPolicy = Field(
        default_factory=ExecutionSafetyPolicy,
        description="Safety rules for sandbox execution."
    )
    allow_symbolic_patches: bool = Field(
        default=True,
        description="Enable deterministic symbolic AST transformation rules."
    )
    allow_llm_patches: bool = Field(
        default=False,
        description="Enable LLM to generate localized patch proposals."
    )
    use_llm_intent: bool = Field(
        default=True,
        description="Gate F dùng LLM (khi repair() được truyền llm): loại bản sửa sai ý câu hỏi "
                    "và chọn giữa các cách nối bảng cho kết quả khác nhau."
    )
    active_gates: List[GateType] = Field(
        default_factory=lambda: [
            GateType.GATE_A_PARSE,
            GateType.GATE_B_SCHEMA_SCOPE,
            GateType.GATE_C_TYPE,
            GateType.GATE_D_SAFETY,
            GateType.GATE_E_EXECUTION,
        ],
        description="List of verification gates that must pass."
    )
