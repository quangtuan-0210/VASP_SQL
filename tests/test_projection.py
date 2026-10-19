"""
Test bản vá bỏ cột thừa (DropSelectColumn) — dùng LLM giả, không cần mạng.
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
from vasp_sql.repair.projection import output_columns, prune_projection


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def chat(self, messages, max_tokens=400, use_cache=True):
        self.prompts.append(messages[-1]["content"])
        return self.answer


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE hero (id INTEGER PRIMARY KEY, name TEXT, height INTEGER, team TEXT);
        INSERT INTO hero VALUES (1, 'Ann', 170, 'X'), (2, 'Bob', 190, 'X'), (3, 'Cid', 180, 'Y'), (4, 'Dee', 180, 'Y');
    """)
    conn.commit()
    conn.close()
    yield path, SchemaMetadata.from_sqlite_db(path, db_id="h")
    os.remove(path)


ON = RepairPolicy(allow_projection_repair=True)


def _ast(sql):
    return sqlglot.parse_one(sql, read="sqlite")


def test_prune_keeps_order_and_rewrites_alias_in_order_by():
    ast = _ast("SELECT name, COUNT(*) AS cnt FROM hero GROUP BY name ORDER BY cnt DESC LIMIT 1")
    new = prune_projection(ast, [0]).sql(dialect="sqlite")
    assert new.startswith("SELECT name FROM hero")
    assert "ORDER BY COUNT(*) DESC" in new


def test_prune_rewrites_positional_order_by():
    new = prune_projection(_ast("SELECT name, height FROM hero ORDER BY 2 DESC"), [0]).sql(dialect="sqlite")
    assert new == "SELECT name FROM hero ORDER BY height DESC"


def test_not_applicable_cases():
    assert output_columns(_ast("SELECT name FROM hero")) == []           # chỉ 1 cột
    assert output_columns(_ast("SELECT * FROM hero")) == []              # có *
    assert output_columns(_ast("SELECT name FROM hero UNION SELECT team FROM hero")) == []  # câu hợp
    assert prune_projection(_ast("SELECT name, height FROM hero"), [0, 1]) is None  # giữ hết = không vá


def test_drop_extra_column_repaired(db):
    path, schema = db
    llm = FakeLLM('{"keep": [1], "confident": true, "reason": "only the name is asked"}')
    res = repair("Who is the tallest hero?", "SELECT name, height FROM hero ORDER BY height DESC LIMIT 1",
                 schema, path, llm=llm, policy=ON)
    assert res.status == RepairStatus.REPAIRED
    assert res.final_sql == "SELECT name FROM hero ORDER BY height DESC LIMIT 1"
    assert res.patches_applied[0].patch_type == "DropSelectColumn"


def test_unconfident_or_keep_all_means_unchanged(db):
    path, schema = db
    sql = "SELECT name, height FROM hero ORDER BY height DESC LIMIT 1"
    for ans in ('{"keep": [1], "confident": false}', '{"keep": [1, 2], "confident": true}', 'garbage'):
        res = repair("Name and height of the tallest hero?", sql, schema, path, llm=FakeLLM(ans), policy=ON)
        assert res.status == RepairStatus.UNCHANGED and res.final_sql == sql


def test_off_by_default(db):
    path, schema = db
    llm = FakeLLM('{"keep": [1], "confident": true}')
    res = repair("Who is the tallest hero?", "SELECT name, height FROM hero ORDER BY height DESC LIMIT 1",
                 schema, path, llm=llm)
    assert res.status == RepairStatus.UNCHANGED and llm.prompts == []


def test_row_count_must_not_change_without_distinct(db):
    path, schema = db
    # bỏ cột trong câu DISTINCT -> ít dòng hơn vẫn được (đây là câu có DISTINCT nên cho phép)
    res = repair("Which teams are there?", "SELECT DISTINCT team, name FROM hero", schema, path,
                 llm=FakeLLM('{"keep": [1], "confident": true}'), policy=ON)
    assert res.status == RepairStatus.REPAIRED and res.final_sql == "SELECT DISTINCT team FROM hero"
