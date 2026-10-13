"""
Các hàm dùng chung cho các script đánh giá VASP-SQL trên BIRD.

- run_sql_full():   chạy truy vấn (chỉ đọc, có giới hạn thời gian) và trả về toàn bộ kết quả
- ex_match():       kiểm tra Execution Accuracy kiểu BIRD (so tập các dòng kết quả)
- first_failing_gate(): chạy Gate A..F trên một câu và báo cổng đầu tiên bị trượt
"""

import os
import sqlite3
import sys
import threading
from typing import Any, List, Optional, Tuple

# Cho phép chạy "python scripts/xxx.py" từ thư mục gốc project
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vasp_sql.ast.parser import SQLParser  # noqa: E402
from vasp_sql.ast.scope import ScopeAnalyzer  # noqa: E402
from vasp_sql.core.schema import SchemaMetadata  # noqa: E402
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor  # noqa: E402
from vasp_sql.verifiers.gates import VerificationPipeline  # noqa: E402


def run_sql_full(db_path: str, sql: str, timeout_s: float = 30.0) -> Tuple[bool, Optional[List[Tuple[Any, ...]]], Optional[str]]:
    """Chạy SQL ở chế độ chỉ đọc và lấy TẤT CẢ các dòng. Trả về (ok, rows, error)."""
    abs_path = os.path.abspath(db_path).replace("\\", "/")
    conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, check_same_thread=False)
    timed_out = [False]

    def _interrupt():
        timed_out[0] = True
        try:
            conn.interrupt()
        except Exception:
            pass

    timer = threading.Timer(timeout_s, _interrupt)
    timer.start()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        return True, rows, None
    except Exception as e:  # noqa: BLE001
        return False, None, ("timeout" if timed_out[0] else str(e))
    finally:
        timer.cancel()
        conn.close()


def ex_match(db_path: str, pred_sql: str, gold_rows: Optional[List[Tuple[Any, ...]]], timeout_s: float = 30.0) -> bool:
    """Tiêu chí EX chính thức của BIRD: set(pred_rows) == set(gold_rows)."""
    if gold_rows is None:
        return False
    ok, rows, _ = run_sql_full(db_path, pred_sql, timeout_s)
    if not ok:
        return False
    return set(rows) == set(gold_rows)


class GateProbe:
    """Chạy Gate A..F theo đúng thứ tự như pipeline.repair() và báo cổng đầu tiên bị trượt."""

    def __init__(self, dialect: str = "sqlite"):
        self.dialect = dialect
        self.parser = SQLParser(default_dialect=dialect)
        self.scope = ScopeAnalyzer(self.parser)
        self.gates = VerificationPipeline(self.parser, self.scope)

    def first_failing_gate(self, question: str, sql: str, schema: SchemaMetadata, db_path: str,
                           evidence: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        g = self.gates
        a = g.verify_gate_a_syntax(sql, dialect=self.dialect)
        if not a.passed:
            return "A", a.message
        ast = self.parser.parse(sql, dialect=self.dialect)
        b = g.verify_gate_b_schema(ast, schema, dialect=self.dialect)
        if not b.passed:
            return "B", b.message
        c = g.verify_gate_c_type(ast, schema)
        if not c.passed:
            return "C", c.message
        sandbox = SQLiteSandboxExecutor(db_path=db_path)
        d = g.verify_gate_d_safety(sql, sandbox)
        if not d.passed:
            return "D", d.message
        e, _ = g.verify_gate_e_execution(sql, sandbox)
        if not e.passed:
            return "E", e.message
        f = g.verify_gate_f_intent(question, sql, evidence)
        if not f.passed:
            return "F", f.message
        return None, None


def available_samples(loader, db_ids: Optional[List[str]] = None):
    """Các mẫu có file .sqlite trên máy (có thể giới hạn theo danh sách db_ids)."""
    out = []
    for s in loader.samples:
        if db_ids and s.db_id not in db_ids:
            continue
        if os.path.exists(loader.get_db_path(s.db_id)):
            out.append(s)
    return out
