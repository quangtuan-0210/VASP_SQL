"""
Test sửa giá trị lọc (ReplacePredicateValue): câu hợp lệ nhưng ra rỗng vì giá trị không khớp dữ liệu.
"""

import os
import sqlite3
import tempfile

import pytest

from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.pipeline import repair


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE schools (id INTEGER PRIMARY KEY, School TEXT, County TEXT, CharterNum TEXT, Code TEXT);
        INSERT INTO schools VALUES
            (1, 'Alpha High', 'Alameda', '0040', 'A1'),
            (2, 'Beta Middle', 'Monterey', '0041', 'B2'),
            (3, 'Gamma Elem', 'Los Angeles', '0400', 'C3');
    """)
    conn.commit()
    conn.close()
    yield path, SchemaMetadata.from_sqlite_db(path, db_id="s")
    os.remove(path)


def test_leading_zeros_fixed(db):
    path, schema = db
    res = repair("School with charter number 40?", "SELECT School FROM schools WHERE CharterNum = '40'", schema, path)
    assert res.status == RepairStatus.REPAIRED
    assert "'0040'" in res.final_sql
    assert res.patches_applied[0].patch_type == "ReplacePredicateValue"


def test_case_and_alias_fixed(db):
    path, schema = db
    res = repair("Schools in Alameda?", "SELECT T1.School FROM schools AS T1 WHERE T1.County = 'alameda'", schema, path)
    assert res.status == RepairStatus.REPAIRED
    assert "'Alameda'" in res.final_sql


def test_typo_in_text_value_fixed(db):
    path, schema = db
    res = repair("Schools in Monterey?", "SELECT School FROM schools WHERE County = 'Monteray'", schema, path)
    assert res.status == RepairStatus.REPAIRED
    assert "'Monterey'" in res.final_sql


def test_existing_value_with_empty_result_is_unchanged(db):
    path, schema = db
    # giá trị có thật, rỗng do điều kiện khác -> không phải lỗi giá trị -> giữ nguyên
    sql = "SELECT School FROM schools WHERE County = 'Alameda' AND CharterNum = '0041'"
    assert repair("q", sql, schema, path).status == RepairStatus.UNCHANGED


def test_no_close_value_is_unchanged(db):
    path, schema = db
    sql = "SELECT School FROM schools WHERE County = 'Sacramento'"
    res = repair("Schools in Sacramento?", sql, schema, path)
    assert res.status == RepairStatus.UNCHANGED
    assert res.final_sql == sql


def test_short_code_typo_not_guessed(db):
    path, schema = db
    # mã ngắn 'A2' gần cả 'A1' lẫn 'B2' -> không đoán
    res = repair("q", "SELECT School FROM schools WHERE Code = 'A2'", schema, path)
    assert res.status == RepairStatus.UNCHANGED


def test_non_empty_query_never_touched(db):
    path, schema = db
    sql = "SELECT School FROM schools WHERE County = 'Alameda'"
    assert repair("q", sql, schema, path).status == RepairStatus.UNCHANGED
