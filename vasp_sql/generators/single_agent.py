"""
G1 — AI sinh SQL một lượt (baseline single-agent), theo mục 5.2 của kế hoạch.

Một lần gọi LLM: đưa câu hỏi + gợi ý (evidence) + schema, nhận về một câu SQL.
Không dùng công cụ, không chạy thử, không tự sửa lại.
"""

import time
from typing import Optional

from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.adapters.single_agent_adapter import SingleAgentAdapter
from vasp_sql.generators.schema_prompt import schema_ddl

SYSTEM = "You are an expert in SQLite. You translate questions about a database into a single SQLite query."

PROMPT = """Database schema:
{schema}

Question: {question}
Hint: {evidence}

Write ONE SQLite query that answers the question. Use the hint when it is relevant.
Return only the SQL inside a ```sql code block, with no explanation."""


class SingleAgentGenerator:
    name = "g1_single_agent"

    def __init__(self, llm, use_evidence: bool = True, max_tokens: int = 600):
        self.llm = llm
        self.use_evidence = use_evidence
        self.max_tokens = max_tokens
        self.adapter = SingleAgentAdapter(generator_name=self.name)

    def generate(self, sample: EvaluationSample, db_path: str) -> GeneratorOutput:
        evidence: Optional[str] = sample.evidence if self.use_evidence else None
        prompt = PROMPT.format(schema=schema_ddl(db_path), question=sample.question,
                               evidence=evidence or "(none)")
        t0 = time.time()
        try:
            rec = self.llm.chat_full([{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": prompt}], max_tokens=self.max_tokens)
        except Exception as e:  # noqa: BLE001 — lỗi mạng / server: ghi nhận là sinh thất bại
            return self.adapter._fallback_on_error(f"LLM error: {e}", sample, None,
                                                   {"latency_ms": round((time.time() - t0) * 1000, 1)})
        usage = rec.get("usage") or {}
        meta = {
            "model": self.llm.model,
            "llm_calls": 1,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "latency_ms": round((rec.get("seconds") or (time.time() - t0)) * 1000, 1),
            "cached": rec.get("cached", False),
            "use_evidence": self.use_evidence,
        }
        return self.adapter.adapt(rec["text"], sample, metadata=meta)
