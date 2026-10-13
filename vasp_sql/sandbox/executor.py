"""
Sandbox chạy SQLite an toàn, phục vụ Gate D (an toàn) và Gate E (chạy thử).
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, List, Optional, Tuple

import sqlglot
from sqlglot import exp

from vasp_sql.core.config import ExecutionSafetyPolicy
from vasp_sql.core.feedback import ExecutionFeedback


class SQLiteSandboxExecutor:
    """
    Chạy câu SQL trên database SQLite theo chính sách an toàn nghiêm ngặt:
    - Kết nối ở chế độ chỉ đọc
    - PRAGMA query_only = ON
    - Kiểm tra trên AST, cấm DDL/DML
    - Giới hạn thời gian chạy bằng sqlite3 interrupt
    - Giới hạn bộ nhớ / số dòng trả về
    """

    def __init__(
        self,
        db_path: str,
        policy: Optional[ExecutionSafetyPolicy] = None
    ):
        self.db_path = db_path
        self.policy = policy or ExecutionSafetyPolicy()

    def check_safety_ast(self, sql: str) -> Tuple[bool, Optional[str]]:
        """
        Gate D: kiểm tra tĩnh trên AST để đảm bảo câu truy vấn chỉ gồm lệnh chỉ đọc.
        """
        if not self.policy.forbid_ddl_dml:
            return True, None

        try:
            parsed = sqlglot.parse(sql, read="sqlite")
        except Exception as e:
            return False, f"SQL syntax parsing failed during safety check: {e}"

        if not parsed:
            return False, "Empty SQL query."

        forbidden_types = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Drop,
            exp.Create,
            exp.Alter,
            exp.Command,
            exp.Pragma,
        )

        for statement in parsed:
            if statement is None:
                continue

            # Kiểm tra câu lệnh gốc hoặc bất kỳ phần con nào có bị cấm không
            if isinstance(statement, forbidden_types):
                return False, f"Forbidden statement type detected: {statement.key.upper()}"

            for node in statement.walk():
                if isinstance(node, forbidden_types):
                    return False, f"Forbidden nested expression detected: {node.key.upper()}"

            # Đảm bảo câu lệnh là truy vấn (Select, Union, ...)
            if not isinstance(statement, (exp.Select, exp.Union, exp.Query)):
                return False, f"Only read-only SELECT / Query statements are allowed, found: {statement.key.upper()}"

        return True, None

    def execute(self, sql: str) -> ExecutionFeedback:
        """
        Chạy truy vấn trong sandbox và trả về ExecutionFeedback chi tiết.
        """
        # Bước 1: Gate D kiểm tra an toàn
        is_safe, safety_err = self.check_safety_ast(sql)
        if not is_safe:
            return ExecutionFeedback(
                is_success=False,
                error_type="safety_violation",
                error_message=safety_err,
                execution_time_ms=0.0
            )

        # Bước 2: mở kết nối
        uri_path = self.db_path
        if not os.path.exists(uri_path) and uri_path != ":memory:":
            return ExecutionFeedback(
                is_success=False,
                error_type="database_not_found",
                error_message=f"Database file does not exist: {uri_path}",
                execution_time_ms=0.0
            )

        start_time = time.perf_counter()
        conn: Optional[sqlite3.Connection] = None
        timer: Optional[threading.Timer] = None

        try:
            if uri_path == ":memory:":
                conn = sqlite3.connect(":memory:")
            else:
                abs_path = os.path.abspath(uri_path).replace("\\", "/")
                conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True)

            cursor = conn.cursor()

            # Ép chế độ chỉ đọc ở tầng SQLite
            if self.policy.read_only:
                cursor.execute("PRAGMA query_only = ON;")

            # Bước 3: đặt bộ hẹn giờ ngắt truy vấn bằng conn.interrupt()
            timeout_occurred = [False]

            def on_timeout():
                timeout_occurred[0] = True
                if conn:
                    try:
                        conn.interrupt()
                    except Exception:
                        pass

            timer = threading.Timer(self.policy.timeout_seconds, on_timeout)
            timer.start()

            # Chạy truy vấn
            cursor.execute(sql)

            # Bước 4: lấy kết quả, tối đa max_rows dòng
            rows = cursor.fetchmany(self.policy.max_rows + 1)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            if timer:
                timer.cancel()

            is_truncated = len(rows) > self.policy.max_rows
            if is_truncated:
                rows = rows[: self.policy.max_rows]

            # Tính mã băm kết quả
            fingerprint = self._compute_fingerprint(rows)
            set_fingerprint = self._compute_fingerprint(
                sorted(rows, key=lambda r: [str(c) if c is not None else "" for c in r])
            )

            # Xem trước 10 dòng đầu
            preview = [list(r) for r in rows[:10]]

            return ExecutionFeedback(
                is_success=True,
                execution_time_ms=round(elapsed_ms, 2),
                row_count=len(rows),
                result_preview=preview,
                result_fingerprint=fingerprint,
                result_set_fingerprint=set_fingerprint,
            )

        except sqlite3.OperationalError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            if timeout_occurred[0]:
                return ExecutionFeedback(
                    is_success=False,
                    error_type="timeout",
                    error_message=f"Query timed out after {self.policy.timeout_seconds} seconds.",
                    execution_time_ms=round(elapsed_ms, 2)
                )

            err_str = str(e).lower()
            err_type = "runtime_error"
            if "no such column" in err_str:
                err_type = "no_such_column"
            elif "no such table" in err_str:
                err_type = "no_such_table"
            elif "syntax error" in err_str:
                err_type = "syntax_error"

            return ExecutionFeedback(
                is_success=False,
                error_type=err_type,
                error_message=str(e),
                execution_time_ms=round(elapsed_ms, 2)
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return ExecutionFeedback(
                is_success=False,
                error_type="unexpected_error",
                error_message=str(e),
                execution_time_ms=round(elapsed_ms, 2)
            )

        finally:
            if timer and timer.is_alive():
                timer.cancel()
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    @staticmethod
    def _compute_fingerprint(rows: List[Tuple[Any, ...]]) -> str:
        """
        Tạo mã băm SHA-256 cố định của kết quả để so sánh.
        """
        # Đổi các giá trị không chuyển thành JSON được sang chuỗi
        normalized = []
        for row in rows:
            normalized_row = [str(col) if col is not None else "NULL" for col in row]
            normalized.append(normalized_row)

        serialized = json.dumps(normalized, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
