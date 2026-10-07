"""
Adapter for G2 - MAC-SQL (Multi-Agent Text-to-SQL framework).
"""

from typing import Any, Dict, Optional

from vasp_sql.adapters.base_adapter import BaseGeneratorAdapter
from vasp_sql.adapters.cleaner import extract_and_clean_sql
from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.core.feedback import ExecutionFeedback


class MacSqlAdapter(BaseGeneratorAdapter):
    """
    Adapter for MAC-SQL multi-agent system outputs.
    Extracts multi-agent metadata (decomposition sub-goals, schema filtering, refinement logs).
    """

    def __init__(self, generator_name: str = "mac_sql"):
        super().__init__(generator_name=generator_name)

    def adapt(
        self,
        raw_output: Any,
        sample: EvaluationSample,
        execution_feedback: Optional[ExecutionFeedback] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> GeneratorOutput:
        try:
            extracted_meta = dict(metadata or {})
            raw_text = ""

            if isinstance(raw_output, dict):
                # MAC-SQL typical JSON keys
                candidate_sql = (
                    raw_output.get("final_sql")
                    or raw_output.get("pred_sql")
                    or raw_output.get("sql")
                    or raw_output.get("query")
                )
                raw_text = str(candidate_sql or raw_output)

                # Capture multi-agent decomposition and schema refinement steps
                if "sub_goals" in raw_output:
                    extracted_meta["sub_goals"] = raw_output["sub_goals"]
                if "sub_problems" in raw_output:
                    extracted_meta["sub_problems"] = raw_output["sub_problems"]
                if "selected_tables" in raw_output:
                    extracted_meta["selected_tables"] = raw_output["selected_tables"]
                if "refinement_rounds" in raw_output:
                    extracted_meta["refinement_rounds"] = raw_output["refinement_rounds"]
                if "agent_history" in raw_output:
                    extracted_meta["agent_history_steps"] = len(raw_output["agent_history"])
            else:
                raw_text = str(raw_output) if raw_output is not None else ""

            cleaned_sql = extract_and_clean_sql(raw_text)
            is_successful = cleaned_sql is not None

            return GeneratorOutput(
                sample_id=sample.question_id,
                db_id=sample.db_id,
                question=sample.question,
                evidence=sample.evidence,
                generator_name=self.generator_name,
                raw_response=raw_text,
                cleaned_sql=cleaned_sql,
                is_generation_successful=is_successful,
                generation_error=None if is_successful else "MAC-SQL produced no valid SQL in output",
                metadata=extracted_meta,
                initial_execution_feedback=execution_feedback
            )

        except Exception as e:
            return self._fallback_on_error(
                error_msg=f"MacSqlAdapter error: {e}",
                sample=sample,
                raw_output=raw_output,
                metadata=metadata
            )
