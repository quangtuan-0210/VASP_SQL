"""
Execution and Parser Feedback representations.
"""

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class ParserFeedback(BaseModel):
    is_valid_syntax: bool = Field(description="Whether the query syntax is valid in the specified dialect.")
    error_message: Optional[str] = Field(default=None, description="Detailed parser error message if any.")
    line: Optional[int] = Field(default=None, description="Error line number if reported by parser.")
    col: Optional[int] = Field(default=None, description="Error column position if reported by parser.")


class ExecutionFeedback(BaseModel):
    is_success: bool = Field(description="Whether the query executed without errors.")
    error_type: Optional[str] = Field(
        default=None,
        description="Category of runtime error: 'syntax_error', 'no_such_table', 'no_such_column', 'type_mismatch', 'timeout', 'safety_violation', etc."
    )
    error_message: Optional[str] = Field(default=None, description="Raw database runtime error message.")
    execution_time_ms: Optional[float] = Field(default=None, description="Elapsed execution time in milliseconds.")
    row_count: Optional[int] = Field(default=None, description="Number of rows returned.")
    result_preview: Optional[List[List[Any]]] = Field(
        default=None,
        description="Truncated sample of returned rows for inspection."
    )
    result_fingerprint: Optional[str] = Field(
        default=None,
        description="Hash/fingerprint of the query result for semantic regression check."
    )
    result_set_fingerprint: Optional[str] = Field(
        default=None,
        description="Order-independent fingerprint (rows sorted) — used to check whether two candidate queries agree."
    )
