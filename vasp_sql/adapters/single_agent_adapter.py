"""
Adapter for G1 - Single-Agent Baseline (One-shot / Zero-shot LLM prompts).
"""

from typing import Any, Dict, Optional

from vasp_sql.adapters.base_adapter import BaseGeneratorAdapter
from vasp_sql.adapters.cleaner import extract_and_clean_sql
from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.core.feedback import ExecutionFeedback


class SingleAgentAdapter(BaseGeneratorAdapter):
    """
    Adapter for baseline single-agent LLMs producing raw SQL or markdown text.
    """

    def __init__(self, generator_name: str = "baseline_single_agent"):
        super().__init__(generator_name=generator_name)

    def adapt(
        self,
        raw_output: Any,
        sample: EvaluationSample,
        execution_feedback: Optional[ExecutionFeedback] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> GeneratorOutput:
        try:
            # Handle if raw_output is dict
            if isinstance(raw_output, dict):
                text_content = (
                    raw_output.get("sql")
                    or raw_output.get("response")
                    or raw_output.get("text")
                    or str(raw_output)
                )
                extracted_meta = {**raw_output.get("metadata", {}), **(metadata or {})}
            else:
                text_content = str(raw_output) if raw_output is not None else ""
                extracted_meta = metadata or {}

            cleaned_sql = extract_and_clean_sql(text_content)
            is_successful = cleaned_sql is not None

            return GeneratorOutput(
                sample_id=sample.question_id,
                db_id=sample.db_id,
                question=sample.question,
                evidence=sample.evidence,
                generator_name=self.generator_name,
                raw_response=text_content,
                cleaned_sql=cleaned_sql,
                is_generation_successful=is_successful,
                generation_error=None if is_successful else "Failed to extract valid SQL from response",
                metadata=extracted_meta,
                initial_execution_feedback=execution_feedback
            )

        except Exception as e:
            return self._fallback_on_error(
                error_msg=f"SingleAgentAdapter error: {e}",
                sample=sample,
                raw_output=raw_output,
                metadata=metadata
            )
