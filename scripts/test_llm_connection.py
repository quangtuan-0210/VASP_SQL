"""
Kiểm tra kết nối tới server LLM / embedding (chuẩn OpenAI API).

Cách chạy (tại thư mục VAST_SQL):
    py scripts/test_llm_connection.py

Thông tin server / API key được đọc từ file .env ở thư mục gốc project
(xem .env.example). Không ghi API key vào code.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vasp_sql.core.env import load_env  # noqa: E402

load_env()

BASE = os.environ.get("VT_BASE_URL", "http://171.232.252.238:8080/v1").rstrip("/")
KEY = os.environ.get("VT_API_KEY", "")
LLM = os.environ.get("VT_MODEL_LLM", "Qwen3.5-122B-A10B")
EMB = os.environ.get("VT_MODEL_EMB", "Qwen3-Embedding-4B")


def call(path, payload=None, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), time.time() - t0
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {body}") from None


def step(name, fn):
    try:
        msg = fn()
        print(f"[OK]   {name}: {msg}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[LỖI] {name}: {type(e).__name__}: {e}")
        return False


def models():
    data, dt = call("/models", timeout=20)
    return f"{[m.get('id') for m in data.get('data', [])]} ({dt:.1f}s)"


PROMPT = "Viết 1 câu SQL đếm số dòng của bảng users. Chỉ trả về SQL."


def _chat(model, extra, max_tokens):
    data, dt = call("/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
        **extra,
    })
    msg = data["choices"][0]["message"]
    text = (msg.get("content") or "").strip()
    think = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
    usage = data.get("usage", {})
    return (f"trả lời={text[:150]!r} | phần suy nghĩ={len(think)} ký tự | "
            f"token ra={usage.get('completion_tokens')} | {dt:.1f}s")


def chat_no_thinking():
    # vLLM / SGLang: tắt chế độ suy nghĩ của Qwen3.x
    return _chat(LLM, {"chat_template_kwargs": {"enable_thinking": False}}, 300)


def chat_with_thinking():
    return _chat(LLM, {}, 4000)


def chat_instruct_model():
    return _chat("Qwen3-235B-A22B-Instruct-2507", {}, 300)


def _embed(payload):
    data, dt = call("/embeddings", {"model": EMB, **payload})
    vecs = [d["embedding"] for d in data["data"]]
    return f"{len(vecs)} vector, kích thước {len(vecs[0])} ({dt:.1f}s)"


def embed_one():
    return _embed({"input": "số học sinh mỗi trường"})


def embed_list():
    return _embed({"input": ["số học sinh mỗi trường", "SELECT COUNT(*) FROM schools"]})


def embed_float():
    return _embed({"input": "số học sinh mỗi trường", "encoding_format": "float"})


if __name__ == "__main__":
    if not KEY:
        sys.exit("Chưa đặt biến môi trường VT_API_KEY")
    print(f"Server: {BASE}")
    tests = [
        ("Danh sách model", models),
        (f"Chat {LLM} — tắt suy nghĩ", chat_no_thinking),
        (f"Chat {LLM} — có suy nghĩ, 4000 token", chat_with_thinking),
        ("Chat Qwen3-235B-A22B-Instruct-2507", chat_instruct_model),
        (f"Embedding {EMB} — 1 câu", embed_one),
        (f"Embedding {EMB} — danh sách 2 câu", embed_list),
        (f"Embedding {EMB} — encoding_format=float", embed_float),
    ]
    ok = [step(n, f) for n, f in tests]
    print(f"\nKết quả: {sum(ok)}/{len(ok)} bước OK")
