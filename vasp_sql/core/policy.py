"""
Định nghĩa chính sách sửa lỗi của VASP-SQL.
"""

from typing import List
from pydantic import BaseModel, Field

from vasp_sql.core.config import ExecutionSafetyPolicy, RepairBudget
from vasp_sql.core.result import GateType


class RepairPolicy(BaseModel):
    """
    Chính sách điều khiển việc sửa lỗi: ngưỡng tin cậy và các cổng kiểm tra được bật.
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
    allow_value_repair: bool = Field(
        default=True,
        description="Khi câu hợp lệ nhưng ra rỗng: thử thay giá trị lọc bằng giá trị thật gần nhất trong database."
    )
    allow_projection_repair: bool = Field(
        default=False,
        description="Bản vá bỏ cột thừa (cần llm): câu chạy được mà SELECT trả thêm cột câu hỏi không hỏi -> "
                    "LLM chọn giữ cột nào (không viết SQL), hệ thống bớt cột trên AST và kiểm chứng lại "
                    "(qua Gate A–E, số dòng giữ nguyên)."
    )
    use_schema_graph: bool = Field(
        default=True,
        description="Dùng đồ thị schema (khóa ngoại) để tìm cách nối bảng khi thiếu JOIN. "
                    "Tắt (cùng allow_value_repair=False và không truyền llm) = cấu hình đối chứng AST-only "
                    "của mục 5.6: chỉ còn quy tắc trên AST + so tên với danh mục bảng/cột."
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
