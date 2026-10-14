"""
G3 — agent nhẹ dùng LangChain (mục 5.2 của kế hoạch).

Dùng SQL agent có sẵn của LangChain (create_sql_agent + SQLDatabaseToolkit, kiểu ReAct):
agent được xem danh sách bảng, xem schema + vài dòng mẫu, chạy thử truy vấn rồi mới trả lời.
- Giới hạn tối đa 10 vòng gọi công cụ (max_iterations) để kiểm soát ngân sách.
  (Bản đầu dùng 5 vòng: 97/300 câu bị dừng giữa chừng vì hết lượt — xem results/generators/g3_mac_dinh.)
- Prompt mặc định của LangChain được giữ nguyên, CHỈ bỏ 2 dòng dặn agent tự giới hạn
  "tối đa {top_k} kết quả" và "sắp xếp để lấy ví dụ thú vị nhất": hai dòng này làm agent tự thêm
  LIMIT 10 / ORDER BY vào câu trả lời (52 câu sai ở bản đầu), trái với yêu cầu của câu hỏi BIRD.
- Database mở ở chế độ CHỈ ĐỌC.
- Mô hình: cùng mô hình như G1 (VT_MODEL_LLM, hiện là DeepSeek-V4-Pro), gọi qua lớp ProxyLLM bên dưới.

Vì sao cần ProxyLLM: server hiện tại (LiteLLM) đặt câu trả lời vào ô `reasoning_content` và không
tách lệnh gọi công cụ ra riêng, nên lớp ChatOpenAI chuẩn của LangChain đọc được chuỗi rỗng.
Agent kiểu ReAct chỉ cần văn bản (Thought / Action / Action Input / Observation), nên bọc
LLMClient của project thành một LLM của LangChain là đủ — có luôn bộ nhớ đệm và đếm chi phí.

Câu trả lời cuối của agent là lời văn, nên câu SQL của G3 = câu SQL CUỐI CÙNG agent đã chạy
thành công bằng công cụ sql_db_query (nếu không có thì lấy SQL trong câu trả lời cuối, nếu có).
"""

import time
from typing import Any, List, Optional

from langchain_core.language_models.llms import LLM

from vasp_sql.adapters.langchain_adapter import LangChainAdapter
from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput

MAX_ITERATIONS = 10
MAX_EXECUTION_SECONDS = 180


def _agent_prefix() -> str:
    """Prompt mặc định của LangChain SQL agent, bỏ 2 dòng tự giới hạn số kết quả."""
    from langchain_community.agent_toolkits.sql.prompt import SQL_PREFIX
    drop = ("always limit your query to at most", "return the most interesting examples")
    return "\n".join(line for line in SQL_PREFIX.split("\n") if not any(d in line for d in drop))


class ProxyLLM(LLM):
    """Bọc LLMClient của project thành LLM của LangChain (chỉ văn bản, có hỗ trợ chuỗi dừng)."""

    client: Any
    max_tokens: int = 2000  # trần mỗi bước; mô hình viết dài (qwen38-27b) bị cắt ở 800
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def _llm_type(self) -> str:
        return "vasp_proxy"

    def _call(self, prompt: str, stop: Optional[List[str]] = None, run_manager=None, **kwargs) -> str:
        rec = self.client.chat_full([{"role": "user", "content": prompt}], max_tokens=self.max_tokens, stop=stop)
        self.calls += 1
        usage = rec.get("usage") or {}
        self.prompt_tokens += usage.get("prompt_tokens", 0) or 0
        self.completion_tokens += usage.get("completion_tokens", 0) or 0
        return rec["text"]


def _last_successful_query(steps) -> Optional[str]:
    """Câu SQL cuối cùng mà agent chạy bằng sql_db_query và KHÔNG báo lỗi."""
    for action, observation in reversed(steps or []):
        tool = getattr(action, "tool", "")
        if tool == "sql_db_query" and not str(observation).lstrip().lower().startswith("error"):
            return str(getattr(action, "tool_input", "")).strip()
    return None


class LangChainSQLGenerator:
    name = "g3_langchain_agent"

    def __init__(self, llm_client, use_evidence: bool = True):
        self.client = llm_client
        self.use_evidence = use_evidence
        self.adapter = LangChainAdapter(generator_name=self.name)

    def generate(self, sample: EvaluationSample, db_path: str) -> GeneratorOutput:
        from langchain_community.agent_toolkits import SQLDatabaseToolkit, create_sql_agent
        from langchain_community.utilities import SQLDatabase

        t0 = time.time()
        llm = ProxyLLM(client=self.client)
        meta = {"model": self.client.model, "max_iterations": MAX_ITERATIONS, "use_evidence": self.use_evidence}
        try:
            uri = f"sqlite:///file:{db_path}?mode=ro&uri=true"   # chỉ đọc
            db = SQLDatabase.from_uri(uri, sample_rows_in_table_info=3, max_string_length=100)
            agent = create_sql_agent(
                llm=llm,
                toolkit=SQLDatabaseToolkit(db=db, llm=llm),
                agent_type="zero-shot-react-description",
                prefix=_agent_prefix(),
                max_iterations=MAX_ITERATIONS,
                max_execution_time=MAX_EXECUTION_SECONDS,
                top_k=10,
                agent_executor_kwargs={"return_intermediate_steps": True, "handle_parsing_errors": True},
            )
            question = sample.question
            if self.use_evidence and sample.evidence:
                question += f"\nHint: {sample.evidence}"
            result = agent.invoke({"input": question})
        except Exception as e:  # noqa: BLE001
            meta.update(llm_calls=llm.calls, latency_ms=round((time.time() - t0) * 1000, 1))
            return self.adapter._fallback_on_error(f"LangChain agent error: {type(e).__name__}: {e}", sample, None, meta)

        steps = result.get("intermediate_steps", [])
        chosen = _last_successful_query(steps)
        final_answer = str(result.get("output", ""))
        raw = {
            "output": f"```sql\n{chosen}\n```" if chosen else final_answer,
            "intermediate_steps": steps,
        }
        meta.update(
            llm_calls=llm.calls,
            prompt_tokens=llm.prompt_tokens,
            completion_tokens=llm.completion_tokens,
            latency_ms=round((time.time() - t0) * 1000, 1),
            tool_steps=[getattr(a, "tool", "") for a, _ in steps],
            final_answer=final_answer[:500],
            hit_iteration_limit="Agent stopped due to iteration limit" in final_answer,
        )
        return self.adapter.adapt(raw, sample, metadata=meta)
