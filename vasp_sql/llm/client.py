"""
Client gọi LLM theo chuẩn API của OpenAI, dùng cho lớp sửa lỗi VASP-SQL.

- Cấu hình lấy từ file .env (VT_BASE_URL, VT_API_KEY, VT_MODEL_LLM) — xem .env.example.
- Xử lý lỗi của server hiện tại (LiteLLM): câu trả lời của Qwen3 Instruct bị đặt vào ô
  `reasoning_content` thay vì `content`, nên đọc cả hai ô.
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
        self.model = model or os.environ.get("VT_MODEL_LLM", "Qwen3-235B-A22B-Instruct-2507")
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature  # 0 = trả lời ổn định, dễ tái lập
        self.cache_path = Path(cache_path) if cache_path else PROJECT_ROOT / "results" / "llm_cache.jsonl"
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
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
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": self.temperature}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if use_cache and key in self._cache:
            self.stats["cache_hits"] += 1
            return self._cache[key]["text"]

        last_err = None
        for attempt in range(self.max_retries):
            t0 = time.time()
            try:
                data = self._post("/chat/completions", payload)
                dt = time.time() - t0
                msg = data["choices"][0]["message"]
                text = (msg.get("content") or "").strip()
                if not text:  # lỗi của server: câu trả lời nằm ở ô "suy nghĩ"
                    text = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                usage = data.get("usage") or {}
                self.stats["calls"] += 1
                self.stats["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
                self.stats["completion_tokens"] += usage.get("completion_tokens", 0) or 0
                self.stats["seconds"] += dt
                rec = {"key": key, "model": self.model, "text": text, "usage": usage, "seconds": round(dt, 2)}
                self._cache[key] = rec
                if use_cache:
                    self._save(rec)
                return text
            except (urllib.error.URLError, TimeoutError, LLMError, KeyError) as e:
                last_err = e
                time.sleep(2 ** attempt)  # chờ 1s, 2s, 4s rồi thử lại
        raise LLMError(f"Gọi LLM thất bại sau {self.max_retries} lần: {last_err}")

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
