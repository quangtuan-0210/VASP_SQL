"""
Unit test cho Giai đoạn 1: mô hình dữ liệu lõi và sandbox chạy SQL an toàn.
"""

import os
import sqlite3
import tempfile
import pytest

from vasp_sql.core.config import ExecutionSafetyPolicy, RepairBudget
from vasp_sql.core.schema import SchemaMetadata, TableMetadata, ColumnMetadata, ForeignKeyRelation
from vasp_sql.core.feedback import ExecutionFeedback, ParserFeedback
from vasp_sql.core.result import RepairResult, RepairStatus, GateType, GateResult, ASTPatchRecord
from vasp_sql.core.policy import RepairPolicy
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor


@pytest.fixture
def sample_sqlite_db():
    """Tạo database SQLite tạm với schema và dữ liệu giống thật."""
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
            name TEXT NOT NULL,
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

    yield path

    if os.path.exists(path):
        os.remove(path)


def test_core_models_instantiation():
    """Kiểm tra tạo các mô hình dữ liệu lõi."""
    col = ColumnMetadata(name="age", data_type="INTEGER", is_primary_key=False)
    table = TableMetadata(name="users")
    table.add_column(col)

    schema = SchemaMetadata(db_id="test_db")
    schema.add_table(table)

    assert schema.has_table("users")
    assert schema.has_table("USERS")  # không phân biệt hoa thường
    assert schema.get_column("users", "age") is not None
    assert schema.find_tables_with_column("age") == ["users"]

    # Kiểm tra RepairResult
    res = RepairResult(
        status=RepairStatus.REPAIRED,
        final_sql="SELECT name FROM users;",
        original_sql="SELECT name FROM usr;",
        patches_applied=[
            ASTPatchRecord(
                patch_type="ReplaceTable",
                original_fragment="usr",
                replacement_fragment="users",
                reason="Table usr does not exist",
                evidence="Schema contains table users"
            )
        ]
    )
    assert res.is_changed
    assert res.status == RepairStatus.REPAIRED


def test_schema_introspection_from_sqlite(sample_sqlite_db):
    """Kiểm tra tự đọc SchemaMetadata từ SQLite bằng PRAGMA."""
    schema = SchemaMetadata.from_sqlite_db(sample_sqlite_db, db_id="company")

    assert schema.has_table("departments")
    assert schema.has_table("employees")

    dept_table = schema.get_table("departments")
    assert dept_table is not None
    assert "dept_id" in [c.lower() for c in dept_table.columns]
    assert dept_table.get_column("dept_id").is_primary_key

    emp_table = schema.get_table("employees")
    assert emp_table is not None
    assert emp_table.get_column("salary").data_type == "REAL"

    # Kiểm tra khóa ngoại
    assert len(schema.foreign_keys) == 1
    fk = schema.foreign_keys[0]
    assert fk.from_table.lower() == "employees"
    assert fk.from_column.lower() == "dept_id"
    assert fk.to_table.lower() == "departments"
    assert fk.to_column.lower() == "dept_id"


def test_sandbox_safe_select(sample_sqlite_db):
    """Kiểm tra chạy một câu SELECT hợp lệ trong sandbox."""
    executor = SQLiteSandboxExecutor(sample_sqlite_db)
    feedback = executor.execute("SELECT name, salary FROM employees WHERE dept_id = 1 ORDER BY salary DESC;")

    assert feedback.is_success
    assert feedback.row_count == 2
    assert feedback.result_preview[0][0] == "Alice"
    assert feedback.result_fingerprint is not None


def test_sandbox_blocks_ddl_and_dml(sample_sqlite_db):
    """Kiểm tra Gate D chặn hẳn các câu nguy hiểm mà không chạy chúng."""
    executor = SQLiteSandboxExecutor(sample_sqlite_db)

    # 1. DROP TABLE
    res_drop = executor.execute("DROP TABLE employees;")
    assert not res_drop.is_success
    assert res_drop.error_type == "safety_violation"

    # 2. DELETE FROM
    res_del = executor.execute("DELETE FROM employees WHERE emp_id = 101;")
    assert not res_del.is_success
    assert res_del.error_type == "safety_violation"

    # 3. UPDATE
    res_upd = executor.execute("UPDATE employees SET salary = 999999;")
    assert not res_upd.is_success
    assert res_upd.error_type == "safety_violation"

    # 4. INSERT
    res_ins = executor.execute("INSERT INTO departments VALUES (3, 'HR');")
    assert not res_ins.is_success
    assert res_ins.error_type == "safety_violation"


def test_sandbox_runtime_error_reporting(sample_sqlite_db):
    """Kiểm tra sandbox phân loại đúng lỗi của câu không hợp lệ."""
    executor = SQLiteSandboxExecutor(sample_sqlite_db)

    # Cột không tồn tại
    res_col = executor.execute("SELECT nonexistent_column FROM employees;")
    assert not res_col.is_success
    assert res_col.error_type == "no_such_column"

    # Bảng không tồn tại
    res_tbl = executor.execute("SELECT * FROM non_existent_table;")
    assert not res_tbl.is_success
    assert res_tbl.error_type == "no_such_table"


def test_sandbox_timeout(sample_sqlite_db):
    """Kiểm tra câu chạy quá lâu bị bộ hẹn giờ ngắt."""
    # Đặt timeout 0,2 giây rồi chạy một CTE đệ quy chạy mãi không dừng
    policy = ExecutionSafetyPolicy(timeout_seconds=0.2)
    executor = SQLiteSandboxExecutor(sample_sqlite_db, policy=policy)

    # CTE đệ quy sinh số vô hạn
    infinite_query = """
    WITH RECURSIVE inf(n) AS (
        SELECT 1
        UNION ALL
        SELECT n + 1 FROM inf
    )
    SELECT count(*) FROM inf;
    """
    feedback = executor.execute(infinite_query)
    assert not feedback.is_success
    assert feedback.error_type == "timeout"
