"""
Adapter cho G3 - LangChain SQL Agent (agent dùng công cụ kiểu ReAct / Plan-and-Solve).
"""

from typing import Any, Dict, Optional

from vasp_sql.adapters.base_adapter import BaseGeneratorAdapter
from vasp_sql.adapters.cleaner import extract_and_clean_sql
from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.core.feedback import ExecutionFeedback


class LangChainAdapter(BaseGeneratorAdapter):
    """
    Adapter cho LangChain SQL Agent có suy luận bằng công cụ.
    Lấy các bước gọi công cụ, kết quả quan sát và câu truy vấn cuối.
    """

    def __init__(self, generator_name: str = "langchain_sql_agent"):
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
            candidate_sql = None
            raw_text = ""

            if isinstance(raw_output, dict):
                # Kết quả dạng dict chuẩn của LangChain agent: {"output": ..., "intermediate_steps": ...}
                output_str = raw_output.get("output", "")
                raw_text = str(output_str)

                # Xem các bước trung gian để tìm câu SQL mà công cụ đã chạy
                intermediate_steps = raw_output.get("intermediate_steps", [])
                extracted_meta["intermediate_step_count"] = len(intermediate_steps)

                tool_queries = []
                for step in intermediate_steps:
                    if isinstance(step, (list, tuple)) and len(step) >= 2:
                        action, observation = step[0], step[1]
                        tool_name = getattr(action, "tool", "") or (action.get("tool") if isinstance(action, dict) else "")
                        tool_input = getattr(action, "tool_input", "") or (action.get("tool_input") if isinstance(action, dict) else "")

                        if "query" in str(tool_name).lower():
                            tool_queries.append(str(tool_input))

                extracted_meta["tool_executed_queries_count"] = len(tool_queries)

                # Ưu tiên 1: SQL nằm trong chuỗi output
                candidate_sql = extract_and_clean_sql(output_str)

                # Ưu tiên 2: nếu output không có câu SELECT rõ ràng, lấy truy vấn ở lần gọi công cụ cuối
                if not candidate_sql and tool_queries:
                    candidate_sql = extract_and_clean_sql(tool_queries[-1])
            else:
                raw_text = str(raw_output) if raw_output is not None else ""
                candidate_sql = extract_and_clean_sql(raw_text)

            is_successful = candidate_sql is not None

            return GeneratorOutput(
                sample_id=sample.question_id,
                db_id=sample.db_id,
                question=sample.question,
                evidence=sample.evidence,
                generator_name=self.generator_name,
                raw_response=raw_text,
                cleaned_sql=candidate_sql,
                is_generation_successful=is_successful,
                generation_error=None if is_successful else "LangChain agent produced no valid SQL in output or tool trace",
                metadata=extracted_meta,
                initial_execution_feedback=execution_feedback
            )

        except Exception as e:
            return self._fallback_on_error(
                error_msg=f"LangChainAdapter error: {e}",
                sample=sample,
                raw_output=raw_output,
                metadata=metadata
            )
