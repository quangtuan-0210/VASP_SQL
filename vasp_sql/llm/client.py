"""
Client gọi LLM theo chuẩn API của OpenAI, dùng cho lớp sửa lỗi VASP-SQL.

- Cấu hình lấy từ file .env (VT_BASE_URL, VT_API_KEY, VT_MODEL_LLM) — xem .env.example.
- Model "suy nghĩ" (VD DeepSeek-V4): đặt VT_REASONING_EFFORT (none/low/high/max); "none" = tắt suy nghĩ
  (gửi thinking={"type": "disabled"});
  phần suy nghĩ cũng tính vào max_tokens nên tự cộng thêm VT_REASONING_BUDGET token (mặc định 4000)
  để câu trả lời không bị cắt cụt. Với model suy nghĩ, ô `reasoning_content` là phần nháp — KHÔNG
  dùng làm câu trả lời.
- Model không suy nghĩ chạy qua LiteLLM cũ (Qwen3 Instruct) đôi khi đặt câu trả lời vào
  `reasoning_content` -> chỉ khi đó mới đọc ô này.
- Mọi câu trả lời được lưu vào bộ nhớ đệm trên đĩa (file JSONL, khoá = mã băm của yêu cầu):
  chạy lại đánh giá không phải gọi server lần nữa, và kết quả tái lập được.
- Đếm số lần gọi / số token / thời gian để báo cáo chi phí và kiểm soát ngân sách.
- Không cần thư viện ngoài (chỉ dùng urllib).
"""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from vasp_sql.core.env import PROJECT_ROOT, load_env


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_path: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        temperature: float = 0.0,
    ):
        load_env()
        self.base_url = (base_url or os.environ.get("VT_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("VT_API_KEY", "")
        self.model = model or os.environ.get("VT_MODEL_LLM", "deepseek-v4-pro")
        self.timeout = float(os.environ.get("VT_LLM_TIMEOUT", timeout))  # server chậm khi tải nặng -> cho chờ lâu hơn
        self.max_retries = max_retries
        self.temperature = temperature  # 0 = trả lời ổn định, dễ tái lập
        self.reasoning_effort = os.environ.get("VT_REASONING_EFFORT", "").strip().lower() or None
        # "none"/"off"/"disabled" = TẮT suy nghĩ (DeepSeek: thinking={"type": "disabled"})
        self.thinking_off = self.reasoning_effort in ("none", "off", "disabled")
        self.reasoning_budget = (int(os.environ.get("VT_REASONING_BUDGET", "4000"))
                                 if self.reasoning_effort and not self.thinking_off else 0)
        self.cache_path = Path(cache_path) if cache_path else PROJECT_ROOT / "results" / "llm_cache.jsonl"
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._tl = threading.local()  # bộ đếm token theo từng câu (mỗi luồng một bộ)
        # Thống kê chi phí (gọi thật, lấy từ cache, token, giây)
        self.stats = {"calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
        self._load_cache()
        if not self.base_url:
            raise LLMError("Chưa có VT_BASE_URL (tạo file .env theo .env.example)")

    # ------------------------------------------------------------ bộ nhớ đệm
    def _load_cache(self):
        if self.cache_path.exists():
            with open(self.cache_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue  # dòng ghi dở do bị ngắt giữa chừng

    def _save(self, rec: dict):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.cache_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ gọi LLM
    def chat(self, messages: List[dict], max_tokens: int = 400, use_cache: bool = True) -> str:
        """Gửi hội thoại, trả về đoạn văn bản trả lời (đã gộp xử lý lỗi 'đặt nhầm ô')."""
        return self.chat_full(messages, max_tokens=max_tokens, use_cache=use_cache)["text"]

    def chat_full(self, messages: List[dict], max_tokens: int = 400, use_cache: bool = True,
                  stop: Optional[List[str]] = None) -> dict:
        """Như chat() nhưng trả về cả bản ghi: text, usage (token), seconds, cached (lấy từ bộ nhớ đệm?)."""
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens + self.reasoning_budget, "temperature": self.temperature}
        if self.thinking_off:
            # không suy nghĩ: rẻ, nhanh, temperature=0 có tác dụng. Gửi cả 2 kiểu tắt vì mỗi server nhận một kiểu
            # (DeepSeek: thinking.type=disabled; server SGLang/Qwen qua gateway: reasoning_effort=none)
            payload["thinking"] = {"type": "disabled"}
            payload["reasoning_effort"] = "none"
        elif self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if stop:
            payload["stop"] = list(stop)  # chuỗi dừng (agent kiểu ReAct cần dừng trước "Observation:")
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if use_cache and key in self._cache:
            self.stats["cache_hits"] += 1
            self._acc(self._cache[key].get("usage") or {}, self._cache[key].get("seconds", 0))
            return {**self._cache[key], "cached": True}

        last_err = None
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                data = self._post("/chat/completions", payload)
                dt = time.time() - t0
                msg = data["choices"][0]["message"]
                text = (msg.get("content") or "").strip()
                if not text and not self.reasoning_effort:  # lỗi server cũ: câu trả lời nằm ở ô "suy nghĩ"
                    text = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                for st in (stop or []):  # phòng khi server bỏ qua tham số stop
                    if st in text:
                        text = text[: text.index(st)]
                usage = data.get("usage") or {}
                self.stats["calls"] += 1
                self.stats["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
                self.stats["completion_tokens"] += usage.get("completion_tokens", 0) or 0
                self.stats["seconds"] += dt
                rec = {"key": key, "model": self.model, "text": text, "usage": usage, "seconds": round(dt, 2)}
                self._acc(usage, dt)
                self._cache[key] = rec
                if use_cache:
                    self._save(rec)
                return {**rec, "cached": False}
            except (urllib.error.URLError, TimeoutError, LLMError, KeyError) as e:
                last_err = e
                time.sleep(2 ** attempt)  # chờ 1s, 2s, 4s rồi thử lại
        raise LLMError(f"Gọi LLM thất bại sau {self.max_retries} lần: {last_err}")

    # ------------------------------------------------------------ chi phí theo từng câu
    def begin_sample(self):
        """Bắt đầu đếm token cho một câu (trong luồng hiện tại)."""
        self._tl.acc = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "llm_seconds": 0.0}

    def end_sample(self) -> dict:
        """Trả về số lượt gọi / token / giây đã dùng cho câu hiện tại (tính cả lượt lấy từ bộ nhớ đệm,
        để chi phí báo cáo đúng bằng chi phí khi chạy lần đầu)."""
        acc = getattr(self._tl, "acc", None) or {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "llm_seconds": 0.0}
        self._tl.acc = None
        acc["llm_seconds"] = round(acc["llm_seconds"], 2)
        return acc

    def _acc(self, usage: dict, seconds: float):
        acc = getattr(self._tl, "acc", None)
        if acc is None:
            return
        acc["calls"] += 1
        acc["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        acc["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        acc["llm_seconds"] += seconds or 0

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            raise LLMError(f"HTTP {e.code}: {body}") from None
