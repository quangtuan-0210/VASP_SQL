"""
G2 — MAC-SQL (hệ nhiều agent: Selector -> Decomposer -> Refiner), mục 5.2 của kế hoạch.

Dùng NGUYÊN code gốc của bài báo (third_party/MAC-SQL, commit 31a9df5, giữ nguyên prompt của 3 agent),
chỉ thay chỗ gọi LLM:
  code gốc thử `from core import api` trước (chỗ cắm LLM tuỳ chỉnh do tác giả để sẵn), không có thì
  dùng OpenAI. Ở đây ta đăng ký một module `core.api` giả, chuyển mọi lời gọi sang LLMClient của
  project (VT_MODEL_LLM — hiện là DeepSeek-V4-Pro, cùng mô hình với G1/G3; có bộ nhớ đệm và đếm chi phí).

Khác biệt so với cấu hình gốc (ghi vào bài báo):
  - Mô hình: DeepSeek-V4-Pro (VT_MODEL_LLM) thay vì GPT-4.
  - temperature 0 (gốc 0.1) để kết quả tái lập được.
  - MAX_ROUND = 3 như gốc (Selector, Decomposer, Refiner mỗi agent một lượt).
  - Database mở CHỈ ĐỌC: code gốc mở bằng sqlite3.connect(đường_dẫn) có quyền ghi, trong khi agent
    Refiner chạy thử SQL do LLM sinh ra — ở đây ép sang chế độ chỉ đọc (nguyên tắc Gate D).
  - Đường dẫn database được truyền dạng tương đối: code gốc dùng data_path.strip('/') nên đường dẫn
    tuyệt đối kiểu Linux (/home/...) bị mất dấu '/' ở đầu (trên Windows 'D:/...' không bị).
"""

import os
import sys
import threading
import time
import types

from vasp_sql.adapters.mac_sql_adapter import MacSqlAdapter
from vasp_sql.adapters.models import EvaluationSample, GeneratorOutput
from vasp_sql.core.env import PROJECT_ROOT

# Giới hạn token mỗi lượt gọi của MAC-SQL. 1500 đủ cho Qwen3-235B nhưng mô hình viết dài (VD qwen38-27b)
# bị cắt trước khi kịp viết khối ```sql -> "No SQL found". Đổi bằng biến VT_MACSQL_MAX_TOKENS.
MACSQL_MAX_TOKENS = int(os.environ.get("VT_MACSQL_MAX_TOKENS", "4000"))
MACSQL_DIR = os.environ.get("VT_MACSQL_DIR", str(PROJECT_ROOT / "third_party" / "MAC-SQL"))

_local = threading.local()      # mỗi luồng: một ChatManager + bộ đếm riêng
_client_holder = {}             # LLMClient dùng chung (an toàn đa luồng)
_install_lock = threading.Lock()


def _install_api_shim(client):
    """Đăng ký module `core.api` giả để code MAC-SQL gọi LLM của project."""
    with _install_lock:
        _client_holder["client"] = client
        if "core.api" in sys.modules:
            return
        if MACSQL_DIR not in sys.path:
            sys.path.insert(0, MACSQL_DIR)
        import core  # package `core` của MAC-SQL

        api = types.ModuleType("core.api")

        def safe_call_llm(input_prompt, **kwargs):
            rec = _client_holder["client"].chat_full([{"role": "user", "content": input_prompt}], max_tokens=MACSQL_MAX_TOKENS)
            stats = getattr(_local, "stats", None)
            if stats is not None:
                usage = rec.get("usage") or {}
                stats["llm_calls"] += 1
                stats["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
                stats["completion_tokens"] += usage.get("completion_tokens", 0) or 0
            return rec["text"]

        def init_log_path(_path):
            return None  # không ghi log riêng; bộ nhớ đệm của LLMClient đã lưu mọi câu trả lời

        api.safe_call_llm = safe_call_llm
        api.init_log_path = init_log_path
        sys.modules["core.api"] = api
        core.api = api

        # Ép mọi kết nối database của MAC-SQL sang chế độ chỉ đọc
        import sqlite3 as _sqlite3
        import core.agents as _agents

        class _ReadOnlySqlite:
            def __getattr__(self, name):
                return getattr(_sqlite3, name)

            @staticmethod
            def connect(path, *a, **kw):
                abs_path = os.path.abspath(path).replace("\\", "/")
                return _sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, check_same_thread=False)

        _agents.sqlite3 = _ReadOnlySqlite()


_DBINFO_LOCK = threading.Lock()
_DBINFO_MEM = {}


def _install_dbinfo_cache():
    """
    Chỉ để chạy nhanh hơn, KHÔNG đổi kết quả: MAC-SQL nạp thông tin từng database (mô tả cột,
    giá trị mẫu) lần đầu gặp db đó, mỗi luồng nạp riêng và mỗi lần chạy lại nạp lại từ đầu
    (db lớn mất hàng phút). Ở đây: dùng chung giữa các luồng + lưu ra đĩa (~/.macsql_dbinfo/)
    để lần chạy sau dùng lại.
    """
    import pickle
    from core.agents import Selector
    if getattr(Selector, "_vasp_cached", False):
        return
    original = Selector._load_single_db_info
    cache_dir = os.path.join(os.path.expanduser("~"), ".macsql_dbinfo")
    os.makedirs(cache_dir, exist_ok=True)

    def cached(self, db_id):
        with _DBINFO_LOCK:
            if db_id in _DBINFO_MEM:
                return _DBINFO_MEM[db_id]
            path = os.path.join(cache_dir, f"{db_id}.pkl")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    info = pickle.load(f)
            else:
                info = original(self, db_id)
                with open(path, "wb") as f:
                    pickle.dump(info, f)
            _DBINFO_MEM[db_id] = info
            return info

    Selector._load_single_db_info = cached
    Selector._vasp_cached = True


def _install_exec_cache():
    """
    Chỉ để chạy được theo từng đoạn ngắn, KHÔNG đổi kết quả: lưu kết quả chạy SQL của bước Refiner
    (SQLite chỉ đọc nên cùng câu SQL luôn ra cùng kết quả) ra đĩa (~/.macsql_exec/). Lần chạy lại
    không phải chạy lại câu SQL chậm. Lỗi quá thời gian (FunctionTimedOut) không được lưu.
    """
    import hashlib, pickle
    from core.agents import Refiner
    if getattr(Refiner, "_vasp_exec_cached", False):
        return
    original = Refiner._execute_sql
    cache_dir = os.path.join(os.path.expanduser("~"), ".macsql_exec")
    os.makedirs(cache_dir, exist_ok=True)

    def cached(self, sql, db_id):
        key = hashlib.sha256(f"{db_id}\n{sql}".encode("utf-8", "ignore")).hexdigest()
        path = os.path.join(cache_dir, key + ".pkl")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return pickle.load(f)
            except Exception:
                pass
        res = original(self, sql, db_id)
        tmp = path + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(res, f)
        os.replace(tmp, path)
        return res

    Refiner._execute_sql = cached
    Refiner._vasp_exec_cached = True


class MacSqlGenerator:
    name = "g2_mac_sql"

    def __init__(self, llm_client, databases_dir: str, tables_json_path: str, use_evidence: bool = True):
        self.client = llm_client
        self.databases_dir = databases_dir
        self.tables_json_path = tables_json_path
        self.use_evidence = use_evidence
        self.adapter = MacSqlAdapter(generator_name=self.name)
        _install_api_shim(llm_client)

    def _manager(self):
        if not hasattr(_local, "manager"):
            from core.chat_manager import ChatManager  # import sau khi đã cắm core.api
            _install_dbinfo_cache()
            _install_exec_cache()
            _local.manager = ChatManager(
                data_path=os.path.relpath(self.databases_dir),   # xem ghi chú đầu file về strip('/')
                tables_json_path=self.tables_json_path,
                log_path=os.path.join(os.path.expanduser("~"), "macsql_unused.log"),
                model_name="qwen", dataset_name="bird", lazy=True,
            )
        return _local.manager

    def generate(self, sample: EvaluationSample, db_path: str) -> GeneratorOutput:
        from core.const import SYSTEM_NAME

        _local.stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        t0 = time.time()
        msg = {
            "idx": sample.question_id, "db_id": sample.db_id, "query": sample.question,
            "evidence": (sample.evidence or "") if self.use_evidence else "",
            "extracted_schema": {}, "ground_truth": "", "difficulty": sample.difficulty,
            "send_to": SYSTEM_NAME,
        }
        meta = {"model": self.client.model, "use_evidence": self.use_evidence}
        try:
            manager = self._manager()
            _local.stats = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}  # bỏ lượt "ping" lúc khởi tạo
            manager.start(msg)
        except Exception as e:  # noqa: BLE001
            meta.update(_local.stats, latency_ms=round((time.time() - t0) * 1000, 1))
            return self.adapter._fallback_on_error(f"MAC-SQL error: {type(e).__name__}: {e}", sample, None, meta)
        pred = (msg.get("pred") or msg.get("final_sql") or "").strip()
        meta.update(
            _local.stats,
            latency_ms=round((time.time() - t0) * 1000, 1),
            pruned=msg.get("pruned"),
            selected_schema=msg.get("extracted_schema"),
            refined=bool(msg.get("fixed")),
            try_times=msg.get("try_times"),
        )
        raw = {"final_sql": pred, "selected_tables": list((msg.get("extracted_schema") or {}).keys())}
        return self.adapter.adapt(raw, sample, metadata=meta)
