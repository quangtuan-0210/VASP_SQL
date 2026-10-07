"""
Unit tests for Phase 3.4: abstain on ambiguity, better name matching, table-typo repair.
"""

import os
import sqlite3
import tempfile

import pytest

from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.pipeline import repair
from vasp_sql.schema_graph import matching


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE colour (id INTEGER PRIMARY KEY, colour TEXT);
        CREATE TABLE superhero (
            id INTEGER PRIMARY KEY, superhero_name TEXT,
            eye_colour_id INTEGER, hair_colour_id INTEGER,
            FOREIGN KEY (eye_colour_id) REFERENCES colour(id),
            FOREIGN KEY (hair_colour_id) REFERENCES colour(id)
        );
        CREATE TABLE district (district_id INTEGER PRIMARY KEY, ALB TEXT, ALP TEXT, A10 INTEGER, A11 INTEGER);
        CREATE TABLE lapTimes (raceId INTEGER, lap INTEGER, milliseconds INTEGER);
        INSERT INTO colour VALUES (1, 'Blue'), (2, 'Blond');
        INSERT INTO superhero VALUES (1, 'Ace', 1, 2), (2, 'Bee', 2, 1);
        INSERT INTO district VALUES (1, 'a', 'b', 10, 11);
        INSERT INTO lapTimes VALUES (1, 1, 90000), (1, 2, 85000);
    """)
    conn.commit()
    conn.close()
    yield path, SchemaMetadata.from_sqlite_db(path, db_id="t")
    os.remove(path)


def test_ambiguous_fk_join_abstains_when_options_disagree(db):
    path, schema = db
    # superhero -> colour via eye_colour_id OR hair_colour_id: the two joins return different rows
    sql = "SELECT T1.superhero_name FROM superhero AS T1 CROSS JOIN colour AS T2 WHERE T2.colour = 'Blond'"
    res = repair("Heroes with blond hair?", sql, schema, path)
    assert res.status == RepairStatus.ABSTAIN
    assert "return different results" in (res.abstain_reason or "")


def test_ambiguous_fk_join_repaired_when_options_agree():
    """Two FKs that identify the same entity (like player_api_id / player_fifa_api_id) give the same rows."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE player (id INTEGER PRIMARY KEY, api_id INTEGER UNIQUE, fifa_id INTEGER UNIQUE, name TEXT);
        CREATE TABLE attrs (id INTEGER PRIMARY KEY, api_id INTEGER, fifa_id INTEGER, rating INTEGER,
            FOREIGN KEY (api_id) REFERENCES player(api_id), FOREIGN KEY (fifa_id) REFERENCES player(fifa_id));
        INSERT INTO player VALUES (1, 100, 900, 'Ann'), (2, 200, 800, 'Bo');
        INSERT INTO attrs VALUES (1, 100, 900, 70), (2, 200, 800, 80), (3, 100, 900, 75);
    """)
    conn.commit()
    conn.close()
    try:
        schema = SchemaMetadata.from_sqlite_db(path, db_id="p")
        sql = "SELECT T2.rating FROM player AS T1 CROSS JOIN attrs AS T2 WHERE T1.name = 'Ann'"
        res = repair("Ann's ratings?", sql, schema, path)
        assert res.status == RepairStatus.REPAIRED, res.abstain_reason
    finally:
        os.remove(path)


def test_swapped_letters_pick_the_right_column(db):
    path, schema = db
    res = repair("q", "SELECT ABL FROM district", schema, path)  # ALB with two letters swapped
    assert res.status == RepairStatus.REPAIRED
    assert "ALB" in res.final_sql and "ALP" not in res.final_sql


def test_equally_close_columns_abstain(db):
    path, schema = db
    res = repair("q", "SELECT A1 FROM district", schema, path)  # A10 or A11?
    assert res.status == RepairStatus.ABSTAIN


def test_table_typo_with_alias_is_repaired(db):
    path, schema = db
    res = repair("q", "SELECT T1.milliseconds FROM lpTimes AS T1 WHERE T1.lap = 1", schema, path)
    assert res.status == RepairStatus.REPAIRED
    assert "lapTimes AS T1" in res.final_sql


def test_table_typo_used_as_qualifier_is_repaired(db):
    path, schema = db
    res = repair("q", "SELECT lapTmes.milliseconds FROM lapTmes WHERE lapTmes.lap = 2", schema, path)
    assert res.status == RepairStatus.REPAIRED
    assert "lapTmes" not in res.final_sql


def test_matching_helpers():
    assert matching.osa_distance("abl", "alb") == 1
    assert matching.osa_distance("abl", "alp") == 2
    best, amb, _ = matching.pick("A1", [(c, c) for c in ["A10", "A11", "A2"]])
    assert best is None and amb
