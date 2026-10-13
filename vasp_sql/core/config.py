"""
Cấu hình và chính sách an toàn cho VASP-SQL.
"""

from typing import Optional
from pydantic import BaseModel, Field


class ExecutionSafetyPolicy(BaseModel):
    """
    Chính sách an toàn mà Gate D áp dụng trước khi chạy bất kỳ câu nào trong sandbox.
    """
    read_only: bool = Field(
        default=True,
        description="Whether queries are executed against a read-only database connection."
    )
    forbid_ddl_dml: bool = Field(
        default=True,
        description="Strictly disallow DDL (CREATE, DROP, ALTER) and DML (INSERT, UPDATE, DELETE)."
    )
    timeout_seconds: float = Field(
        default=5.0,
        description="Maximum execution time in seconds before query is terminated."
    )
    max_rows: int = Field(
        default=1000,
        description="Maximum number of rows returned to prevent out-of-memory errors."
    )
    max_memory_mb: Optional[int] = Field(
        default=512,
        description="Maximum memory allocated for database session if supported by backend."
    )


class RepairBudget(BaseModel):
    """
    Ngân sách tài nguyên khi tìm ứng viên sửa, để so sánh công bằng.
    """
    max_candidates: int = Field(
        default=5,
        description="Maximum number of patch candidates evaluated per query."
    )
    max_llm_calls: int = Field(
        default=2,
        description="Maximum number of LLM invocations allowed during repair."
    )
    max_time_seconds: float = Field(
        default=15.0,
        description="Overall timeout for the repair process on a single query."
    )
    max_ast_edit_nodes: int = Field(
        default=10,
        description="Maximum AST nodes allowed to be modified to enforce locality."
    )


class BenchmarkConfig(BaseModel):
    """
    Cấu hình benchmark cho các lần đánh giá (VD BIRD trên SQLite).
    """
    benchmark_name: str = Field(default="BIRD", description="Name of benchmark suite.")
    dialect: str = Field(default="sqlite", description="Target SQL dialect.")
    db_root_path: Optional[str] = Field(default=None, description="Path to folder containing database files.")
    split: str = Field(default="dev", description="Benchmark split: train, dev, or test.")
