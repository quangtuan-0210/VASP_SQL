"""
Unit test cho Giai đoạn 3.3: bản vá hiểu tên tắt và kiểm tra nối bảng theo từng SELECT.
Test hồi quy cho các lỗi tìm ra bởi scripts/eval_gold.py và scripts/eval_corrupted.py trên BIRD.
"""

import os
import sqlite3
import tempfile

import pytest

from vasp_sql.ast.parser import SQLParser
from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.pipeline import repair
from vasp_sql.schema_graph.graph import SchemaKnowledgeGraph
from vasp_sql.verifiers.gates import VerificationPipeline


@pytest.fixture
def hero_db():
    """superhero -< hero_power >- superpower, cùng khóa ngoại kiểu departments/employees."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE superpower (id INTEGER PRIMARY KEY, power_name TEXT);
        CREATE TABLE superhero (id INTEGER PRIMARY KEY, superhero_name TEXT, height_cm INTEGER);
        CREATE TABLE hero_power (
            hero_id INTEGER, power_id INTEGER,
            FOREIGN KEY (hero_id) REFERENCES superhero(id),
            FOREIGN KEY (power_id) REFERENCES superpower(id)
        );
        INSERT INTO superpower VALUES (1, 'Flight'), (2, 'Adaptation'), (3, 'Speed');
        INSERT INTO superhero VALUES (10, 'Hawk', 180), (11, 'Chameleon', 170), (12, 'Bolt', 175);
        INSERT INTO hero_power VALUES (10, 1), (11, 2), (12, 3), (12, 1);
    """)
    conn.commit()
    conn.close()
    schema = SchemaMetadata.from_sqlite_db(path, db_id="hero")
    yield path, schema
    os.remove(path)


def run(db, sql):
    conn = sqlite3.connect(db)
    try:
        return set(conn.execute(sql).fetchall())
    finally:
        conn.close()


def test_column_typo_behind_alias_is_repaired(hero_db):
    db, schema = hero_db
    gold = "SELECT T1.superhero_name FROM superhero AS T1 WHERE T1.height_cm > 172"
    buggy = "SELECT T1.superhero_name FROM superhero AS T1 WHERE T1.heigth_cm > 172"
    res = repair("Heroes taller than 172cm?", buggy, schema, db)
    assert res.status == RepairStatus.REPAIRED
    assert "T1.height_cm" in res.final_sql
    assert run(db, res.final_sql) == run(db, gold)


def test_unqualified_typo_uses_alias_of_table_in_query(hero_db):
    db, schema = hero_db
    buggy = ("SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_power AS T2 "
             "ON T1.id = T2.hero_id WHERE heigth_cm > 172")
    res = repair("Heroes taller than 172cm?", buggy, schema, db)
    assert res.status == RepairStatus.REPAIRED
    assert "T1.height_cm" in res.final_sql


def test_dropped_join_in_three_table_query_is_detected_and_repaired(hero_db):
    db, schema = hero_db
    gold = ("SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
            "INNER JOIN superpower AS T3 ON T2.power_id = T3.id WHERE T3.power_name = 'Adaptation'")
    buggy = ("SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
             "CROSS JOIN superpower AS T3 WHERE T3.power_name = 'Adaptation'")
    res = repair("Which hero has Adaptation?", buggy, schema, db)
    assert res.status == RepairStatus.REPAIRED, res.abstain_reason
    assert "T2.power_id = T3.id" in res.final_sql
    assert run(db, res.final_sql) == run(db, gold)


def test_dropped_join_with_aliases_two_tables(hero_db):
    db, schema = hero_db
    buggy = "SELECT T2.power_id FROM superhero AS T1 CROSS JOIN hero_power AS T2 WHERE T1.superhero_name = 'Bolt'"
    gold = ("SELECT T2.power_id FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id "
            "WHERE T1.superhero_name = 'Bolt'")
    res = repair("Power ids of Bolt?", buggy, schema, db)
    assert res.status == RepairStatus.REPAIRED, res.abstain_reason
    assert run(db, res.final_sql) == run(db, gold)


def test_subquery_tables_are_not_flagged_as_unjoined(hero_db):
    db, schema = hero_db
    sql = ("SELECT superhero_name FROM superhero WHERE id IN "
           "(SELECT hero_id FROM hero_power WHERE power_id = 1)")
    res = repair("Heroes that can fly?", sql, schema, db)
    assert res.status == RepairStatus.UNCHANGED


def test_gate_b_rejects_cross_join_hidden_among_other_joins(hero_db):
    _, schema = hero_db
    p = SQLParser()
    gates = VerificationPipeline(p)
    ast = p.parse("SELECT 1 FROM superhero AS T1 JOIN hero_power AS T2 ON T1.id = T2.hero_id CROSS JOIN superpower AS T3")
    assert not gates.verify_gate_b_schema(ast, schema).passed


def test_join_path_columns_follow_walk_direction(hero_db):
    _, schema = hero_db
    kg = SchemaKnowledgeGraph(schema)
    fwd = kg.find_join_path("hero_power", "superpower")[0]
    back = kg.find_join_path("superpower", "hero_power")[0]
    assert (fwd.from_table, fwd.from_column, fwd.to_column) == ("hero_power", "power_id", "id")
    assert (back.from_table, back.from_column, back.to_column) == ("superpower", "id", "power_id")


def test_select_alias_and_derived_table_outputs_are_not_flagged(hero_db):
    db, schema = hero_db
    queries = [
        # Tên tắt của SELECT được dùng lại trong ORDER BY
        "SELECT T1.superhero_name, COUNT(T2.power_id) AS n_powers FROM superhero AS T1 "
        "INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id GROUP BY T1.id ORDER BY n_powers DESC LIMIT 1",
        # cột đầu ra của bảng con được dùng ở truy vấn ngoài và trong HAVING
        "SELECT AVG(cnt) FROM (SELECT hero_id, COUNT(*) AS cnt FROM hero_power GROUP BY hero_id HAVING cnt >= 1) AS T",
        # cột đầu ra của CTE
        "WITH t AS (SELECT hero_id, COUNT(*) AS cnt FROM hero_power GROUP BY hero_id) SELECT MAX(cnt) FROM t",
    ]
    for sql in queries:
        res = repair("q", sql, schema, db)
        assert res.status == RepairStatus.UNCHANGED, (sql, res.abstain_reason)
