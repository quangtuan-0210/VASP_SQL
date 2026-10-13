"""
Unit test cho Giai đoạn 3.2: toàn bộ pipeline sửa lỗi VASP-SQL từ đầu đến cuối.
"""

import os
import sqlite3
import tempfile
import pytest

from vasp_sql.core.result import RepairStatus
from vasp_sql.core.schema import ColumnMetadata, ForeignKeyRelation, SchemaMetadata, TableMetadata
from vasp_sql.pipeline import repair


@pytest.fixture
def company_db():
    """Tạo database SQLite tạm để kiểm tra pipeline sửa lỗi."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE departments (
            dept_id INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE employees (
            emp_id INTEGER PRIMARY KEY,
            emp_name TEXT NOT NULL,
            salary REAL,
            dept_id INTEGER,
            FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
        );
    """)

    cur.executemany("INSERT INTO departments VALUES (?, ?);", [
        (1, "Engineering"),
        (2, "Marketing"),
    ])

    cur.executemany("INSERT INTO employees VALUES (?, ?, ?, ?);", [
        (101, "Alice", 95000.0, 1),
        (102, "Bob", 72000.0, 1),
        (103, "Charlie", 68000.0, 2),
    ])

    conn.commit()
    conn.close()

    schema = SchemaMetadata.from_sqlite_db(path, db_id="corp")

    yield path, schema

    if os.path.exists(path):
        os.remove(path)


def test_pipeline_unchanged_on_valid_query(company_db):
    """Kiểm tra câu hợp lệ được giữ nguyên (UNCHANGED)."""
    db_path, schema = company_db

    valid_sql = "SELECT emp_name, salary FROM employees WHERE salary > 70000;"
    result = repair(
        question="Which employees make more than 70k?",
        sql=valid_sql,
        schema=schema,
        db_path=db_path
    )

    assert result.status == RepairStatus.UNCHANGED
    assert result.final_sql.strip() == valid_sql.strip()
    assert result.confidence == 1.0
    assert len(result.patches_applied) == 0


def test_pipeline_repairs_misspelled_column(company_db):
    """Kiểm tra sửa câu có tên cột gõ sai bằng bản vá trên AST."""
    db_path, schema = company_db

    # Gõ sai: 'employe_nam' thay vì 'emp_name'
    buggy_sql = "SELECT employe_nam, salary FROM employees WHERE salary > 70000;"
    result = repair(
        question="Which employees make more than 70k?",
        sql=buggy_sql,
        schema=schema,
        db_path=db_path
    )

    assert result.status == RepairStatus.REPAIRED
    assert "emp_name" in result.final_sql.lower()
    assert len(result.patches_applied) == 1
    assert result.patches_applied[0].patch_type == "ReplaceColumn"
    assert result.is_changed


def test_pipeline_repairs_missing_join(company_db):
    """Kiểm tra sửa câu tham chiếu hai bảng mà thiếu điều kiện nối."""
    db_path, schema = company_db

    # Câu lỗi: tích Descartes / thiếu điều kiện nối
    buggy_sql = "SELECT employees.emp_name, departments.dept_name FROM employees, departments;"
    result = repair(
        question="List employee names and their department names.",
        sql=buggy_sql,
        schema=schema,
        db_path=db_path
    )

    assert result.status == RepairStatus.REPAIRED
    assert "join" in result.final_sql.lower()
    assert "dept_id" in result.final_sql.lower()
    assert any(p.patch_type == "AddJoin" for p in result.patches_applied)


def test_pipeline_abstains_on_irrecoverable_query(company_db):
    """Kiểm tra hệ thống từ chối sửa an toàn khi câu hoàn toàn không cứu được."""
    db_path, schema = company_db

    # Câu vô nghĩa, tham chiếu bảng/cột hoàn toàn không tồn tại
    nonsense_sql = "SELECT fake_field_xyz FROM non_existent_table_999;"
    result = repair(
        question="What is the profit?",
        sql=nonsense_sql,
        schema=schema,
        db_path=db_path
    )

    assert result.status == RepairStatus.ABSTAIN
    assert result.abstain_reason is not None
    assert not result.is_changed
