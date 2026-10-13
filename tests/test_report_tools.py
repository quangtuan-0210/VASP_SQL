"""Test cho các công cụ báo cáo: chỉ số AST mới, McNemar, đếm token theo câu, chế độ tắt suy nghĩ."""
import os
import sys

import sqlglot

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from vasp_sql.ast.parser import SQLParser  # noqa: E402


def _n(a, b):
    return SQLParser.calculate_ast_nodes_changed(sqlglot.parse_one(a, read="sqlite"), sqlglot.parse_one(b, read="sqlite"))


def test_ast_nodes_changed_counts_renames():
    assert _n("SELECT a FROM t", "SELECT a FROM t") == 0
    assert _n("SELECT nam FROM t", "SELECT name FROM t") == 1          # đổi tên cột = 1 nút
    assert _n("SELECT a FROM schoolz", "SELECT a FROM schools") == 1    # đổi tên bảng = 1 nút
    assert _n("SELECT a FROM t WHERE c = 'Pragu'", "SELECT a FROM t WHERE c = 'Prague'") == 1
    assert _n("SELECT x FROM a, b", "SELECT x FROM a JOIN b ON a.id = b.aid") > 1  # thêm điều kiện nối


def test_old_budget_metric_unchanged():
    # chỉ số ngân sách cũ (dùng khi đóng băng) vẫn giữ hành vi cũ: không đếm đổi tên
    a, b = sqlglot.parse_one("SELECT nam FROM t"), sqlglot.parse_one("SELECT name FROM t")
    assert SQLParser.calculate_ast_edit_size(a, b) == 0


def test_mcnemar_exact():
    from stats_report import mcnemar_exact
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0
    assert abs(mcnemar_exact(5, 0) - 0.0625) < 1e-9
    assert mcnemar_exact(6, 0) < 0.05


def test_llm_client_thinking_off_and_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("VT_API_KEY", "x")
    monkeypatch.setenv("VT_MODEL_LLM", "m")
    monkeypatch.setenv("VT_REASONING_EFFORT", "none")
    from vasp_sql.llm.client import LLMClient
    c = LLMClient(cache_path=str(tmp_path / "cache.jsonl"))
    sent = {}

    def fake_post(path, payload):
        sent.update(payload)
        return {"choices": [{"message": {"content": "SELECT 1", "reasoning_content": "nháp"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    monkeypatch.setattr(c, "_post", fake_post)
    c.begin_sample()
    assert c.chat([{"role": "user", "content": "q"}], max_tokens=50) == "SELECT 1"
    assert sent["thinking"] == {"type": "disabled"} and sent["reasoning_effort"] == "none" and sent["max_tokens"] == 50
    c.chat([{"role": "user", "content": "q"}], max_tokens=50)  # lần 2 lấy từ bộ nhớ đệm, vẫn được đếm
    u = c.end_sample()
    assert u["calls"] == 2 and u["prompt_tokens"] == 20 and u["completion_tokens"] == 6


def test_llm_client_reasoning_does_not_leak_draft(monkeypatch, tmp_path):
    # model suy nghĩ: nếu content rỗng (bị cắt), KHÔNG lấy phần nháp làm câu trả lời
    monkeypatch.setenv("VT_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("VT_REASONING_EFFORT", "low")
    from vasp_sql.llm.client import LLMClient
    c = LLMClient(cache_path=str(tmp_path / "cache.jsonl"))
    monkeypatch.setattr(c, "_post", lambda p, pl: {"choices": [{"message": {"content": "", "reasoning_content": "nháp dài"}}], "usage": {}})
    assert c.chat([{"role": "user", "content": "q"}], max_tokens=10) == ""
