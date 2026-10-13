"""
Test hướng 2 — sửa lỗi ý nghĩa cục bộ bằng LLM (dùng LLM giả, không cần mạng).
"""

import os
import sqlite3
import tempfile

import pytest
import sqlglot

from vasp_sql.core.policy import RepairPolicy
from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.pipeline import repair
from vasp_sql.repair.semantic import apply_edits


class FakeLLM:
    """Trả lời lượt 'đề xuất' và lượt 'kiểm chứng' theo hai chuỗi cho trước."""

    def __init__(self, propose, verify):
        self.propose, self.verify, self.prompts = propose, verify, []

    def chat(self, messages, max_tokens=400, use_cache=True):
        p = messages[-1]["content"]
        self.prompts.append(p)
        if "Two candidate SQL queries" in p:
            return self.verify(p) if callable(self.verify) else self.verify
        return self.propose


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE scores (id INTEGER PRIMARY KEY, school TEXT, county TEXT, avg_read INTEGER);
        INSERT INTO scores VALUES (1, 'A', 'X', 400), (2, 'B', 'X', 650), (3, 'C', 'Y', 500), (4, 'C', 'Y', 520);
    """)
    conn.commit()
    conn.close()
    yield path, SchemaMetadata.from_sqlite_db(path, db_id="s")
    os.remove(path)


ON = RepairPolicy(allow_semantic_repair=True)
WRONG = "SELECT school FROM scores ORDER BY avg_read DESC LIMIT 1"
PROPOSE_ASC = '{"correct": false, "edits": [{"op": "replace_expression", "old": "avg_read DESC", "new": "avg_read ASC"}], "reason": "lowest"}'


def pick_repair(prompt):
    # chọn phương án KHÔNG có "DESC" (bản sửa; sqlglot bỏ chữ ASC vì là mặc định), dù ở vị trí 1 hay 2
    opt1 = prompt.split("Option 1:")[1].split("Option 2:")[0]
    return '{"option": 2}' if "DESC" in opt1 else '{"option": 1}'


def test_semantic_repair_accepted_when_judge_agrees(db):
    path, schema = db
    llm = FakeLLM(PROPOSE_ASC, pick_repair)
    res = repair("Which school has the lowest reading score?", WRONG, schema, path, llm=llm, policy=ON)
    assert res.status == RepairStatus.REPAIRED
    assert "DESC" not in res.final_sql and res.final_sql.count("LIMIT 1") == 1
    assert res.patches_applied[0].patch_type == "SemanticEdit"
    assert res.metrics["llm_calls"] == 2


def test_semantic_repair_rejected_when_judge_prefers_original(db):
    path, schema = db
    llm = FakeLLM(PROPOSE_ASC, '{"option": 0}')
    res = repair("Which school has the lowest reading score?", WRONG, schema, path, llm=llm, policy=ON)
    assert res.status == RepairStatus.UNCHANGED and res.final_sql == WRONG


def test_llm_says_correct_means_one_call_and_unchanged(db):
    path, schema = db
    llm = FakeLLM('{"correct": true}', '{"option": 2}')
    res = repair("q", "SELECT school FROM scores", schema, path, llm=llm, policy=ON)
    assert res.status == RepairStatus.UNCHANGED and len(llm.prompts) == 1


def test_off_by_default(db):
    path, schema = db
    llm = FakeLLM(PROPOSE_ASC, '{"option": 2}')
    res = repair("Which school has the lowest reading score?", WRONG, schema, path, llm=llm)
    assert res.status == RepairStatus.UNCHANGED and llm.prompts == []


def test_unknown_or_unlocatable_edit_is_ignored(db):
    path, schema = db
    bad = '{"correct": false, "edits": [{"op": "rewrite_all", "new": "SELECT 1"}]}'
    res = repair("q", WRONG, schema, path, llm=FakeLLM(bad, '{"option": 2}'), policy=ON)
    assert res.status == RepairStatus.UNCHANGED
    bad2 = '{"correct": false, "edits": [{"op": "replace_expression", "old": "no_such_thing", "new": "x"}]}'
    res = repair("q", WRONG, schema, path, llm=FakeLLM(bad2, '{"option": 2}'), policy=ON)
    assert res.status == RepairStatus.UNCHANGED


def test_apply_edit_ops():
    ast = sqlglot.parse_one("SELECT T1.school, T1.county FROM scores AS T1 WHERE T1.county = 'X' AND T1.avg_read > 100", read="sqlite")
    t, _ = apply_edits(ast, [{"op": "remove_condition", "old": "T1.avg_read > 100"}])
    s = t.sql(dialect="sqlite")
    assert s == "SELECT T1.school, T1.county FROM scores AS T1 WHERE T1.county = 'X'"
    # thao tác thay cả danh sách SELECT đã bị tắt
    t, notes = apply_edits(ast, [{"op": "replace_select_list", "new": "T1.school"}])
    assert t is None
    t, _ = apply_edits(ast, [{"op": "add_condition", "new": "T1.avg_read >= 500"}, {"op": "set_distinct", "value": True}])
    assert "SELECT DISTINCT" in t.sql() and "T1.avg_read >= 500" in t.sql()
    t, _ = apply_edits(ast, [{"op": "set_limit", "limit": 2, "offset": 9}])
    assert t.sql(dialect="sqlite").endswith("LIMIT 2 OFFSET 9")
