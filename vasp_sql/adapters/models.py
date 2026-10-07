"""
Standardized Data Models for Generators and Evaluation Samples in VASP-SQL.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from vasp_sql.core.feedback import ExecutionFeedback


class EvaluationSample(BaseModel):
    """
    Standardized benchmark question sample (from BIRD or other benchmarks).
    """
    question_id: int = Field(description="Unique question identifier.")
    db_id: str = Field(description="Database identifier associated with this question.")
    question: str = Field(description="Natural language question.")
    evidence: Optional[str] = Field(default=None, description="External domain knowledge or hints.")
    golden_sql: Optional[str] = Field(default=None, description="Ground truth SQL query for evaluation.")
    difficulty: Optional[str] = Field(default="simple", description="Complexity level (simple, moderate, challenging).")


class GeneratorOutput(BaseModel):
    """
    Standardized representation of the output produced by any SQL generator (G1, G2, G3).
    This serves as the clean, uniform input contract for the VASP-SQL repair layer.
    """
    sample_id: int = Field(description="ID of the evaluation sample.")
    db_id: str = Field(description="Target database identifier.")
    question: str = Field(description="Natural language question.")
    evidence: Optional[str] = Field(default=None, description="External domain knowledge/hints if present.")
    generator_name: str = Field(description="Name or ID of the generator (e.g. 'single_agent', 'mac_sql', 'langchain').")
    
    raw_response: str = Field(description="Original, unmodified response string or serialized response from the generator.")
    cleaned_sql: Optional[str] = Field(default=None, description="Cleaned, isolated SQL query ready for parsing/execution.")
    
    is_generation_successful: bool = Field(
        default=True,
        description="Whether the generator succeeded in producing a candidate SQL query."
    )
    generation_error: Optional[str] = Field(
        default=None,
        description="Error details if the generator failed or timed out."
    )
    
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operational metrics: latency_ms, total_tokens, model_name, intermediate_step_count, etc."
    )
    
    initial_execution_feedback: Optional[ExecutionFeedback] = Field(
        default=None,
        description="Initial execution feedback if the generator ran the query internally."
    )
