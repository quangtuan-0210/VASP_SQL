"""
Test Gate F (kiểm tra ý nghĩa bằng LLM) — dùng LLM giả nên không cần mạng.
"""

import os
import sqlite3
import tempfile

import pytest

from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.pipeline import repair
from vasp_sql.verifiers.intent import _parse_json


class FakeLLM:
    """LLM giả: trả lời theo hàm `rule(prompt)` do test truyền vào, đếm số lần gọi."""

    def __init__(self, rule):
        self.rule = rule
        self.prompts = []

    def chat(self, messages, max_tokens=400, use_cache=True):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        return self.rule(prompt)


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE colour (id INTEGER PRIMARY KEY, colour TEXT);
        CREATE TABLE superhero (
            id INTEGER PRIMARY KEY, superhero_name TEXT, height_cm INTEGER, weight_kg INTEGER,
            eye_colour_id INTEGER, hair_colour_id INTEGER,
            FOREIGN KEY (eye_colour_id) REFERENCES colour(id),
            FOREIGN KEY (hair_colour_id) REFERENCES colour(id)
        );
        INSERT INTO colour VALUES (1, 'Blue'), (2, 'Blond');
        INSERT INTO superhero VALUES (1, 'Ace', 180, 80, 1, 2), (2, 'Bee', 170, 60, 2, 1);
    """)
    conn.commit()
    conn.close()
    yield path, SchemaMetadata.from_sqlite_db(path, db_id="t")
    os.remove(path)


AMBIGUOUS = "SELECT T1.superhero_name FROM superhero AS T1 CROSS JOIN colour AS T2 WHERE T2.colour = 'Blond'"


def test_without_llm_ambiguous_join_still_abstains(db):
    path, schema = db
    assert repair("Heroes with blond hair?", AMBIGUOUS, schema, path).status == RepairStatus.ABSTAIN


def test_llm_chooses_the_join_that_matches_the_question(db):
    path, schema = db

    def rule(prompt):
        # chọn phương án có hair_colour_id
        options = [blk for blk in prompt.split("Option ")[1:]]
        for i, blk in enumerate(options, 1):
            if "hair_colour_id" in blk.split("\n\n")[0]:
                return '```json\n{"option": %d, "reason": "question asks about hair"}\n```' % i
        return '{"option": 0}'

    llm = FakeLLM(rule)
    res = repair("Heroes with blond hair?", AMBIGUOUS, schema, path, llm=llm)
    assert res.status == RepairStatus.REPAIRED, res.abstain_reason
    assert "hair_colour_id" in res.final_sql
    assert res.metrics["llm_calls"] == 1


def test_llm_unsure_choice_abstains(db):
    path, schema = db
    res = repair("Heroes with blond hair?", AMBIGUOUS, schema, path, llm=FakeLLM(lambda p: '{"option": 0}'))
    assert res.status == RepairStatus.ABSTAIN


def test_intent_check_rejects_wrong_meaning(db):
    path, schema = db
    # 'weigth_kg' sửa thành weight_kg: chạy được, nhưng LLM giả nói "no" -> từ chối sửa
    sql = "SELECT superhero_name FROM superhero WHERE weigth_kg > 70"
    llm = FakeLLM(lambda p: '{"verdict": "no", "reason": "question asks about height"}')
    res = repair("Heroes taller than 175 cm?", sql, schema, path, llm=llm)
    assert res.status == RepairStatus.ABSTAIN
    assert "intent check" in res.abstain_reason


def test_intent_check_accepts_and_records_reason(db):
    path, schema = db
    sql = "SELECT superhero_name FROM superhero WHERE heigth_cm > 175"
    llm = FakeLLM(lambda p: 'Sure! {"verdict": "yes", "reason": "filters on height"}')
    res = repair("Heroes taller than 175 cm?", sql, schema, path, llm=llm)
    assert res.status == RepairStatus.REPAIRED
    assert "height_cm" in res.final_sql
    assert any("filters on height" in (g.message or "") for g in res.gate_results)


def test_correct_query_never_calls_llm(db):
    path, schema = db
    llm = FakeLLM(lambda p: '{"verdict": "no"}')
    res = repair("q", "SELECT superhero_name FROM superhero", schema, path, llm=llm)
    assert res.status == RepairStatus.UNCHANGED
    assert llm.prompts == []


def test_parse_json_tolerates_noise():
    assert _parse_json('blah ```json\n{"verdict": "yes"}\n``` end') == {"verdict": "yes"}
    assert _parse_json("no json here") == {}
