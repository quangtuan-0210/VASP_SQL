"""
Lớp adapter cơ sở (trừu tượng) cho các AI sinh SQL.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.adapters.cleaner import extract_and_clean_sql


class BaseGeneratorAdapter(ABC):
    """
    Chuẩn hoá đầu ra thô của nhiều AI sinh SQL khác nhau về cùng dạng GeneratorOutput.
    """

    def __init__(self, generator_name: str):
        self.generator_name = generator_name

    @abstractmethod
    def adapt(
        self,
        raw_output: Any,
        sample: EvaluationSample,
        execution_feedback: Optional[Any] = None,
        metadata: Optional[dict] = None
    ) -> GeneratorOutput:
        """
        Chuyển đầu ra riêng của từng AI sinh SQL thành GeneratorOutput chuẩn.
        """
        pass

    def _fallback_on_error(
        self,
        error_msg: str,
        sample: EvaluationSample,
        raw_output: Any,
        metadata: Optional[dict] = None
    ) -> GeneratorOutput:
        """
        Tạo GeneratorOutput báo thất bại khi gặp lỗi hoặc câu trả lời không đọc được.
        """
        return GeneratorOutput(
            sample_id=sample.question_id,
            db_id=sample.db_id,
            question=sample.question,
            evidence=sample.evidence,
            generator_name=self.generator_name,
            raw_response=str(raw_output) if raw_output is not None else "",
            cleaned_sql=None,
            is_generation_successful=False,
            generation_error=error_msg,
            metadata=metadata or {}
        )
