"""
Chẩn đoán "giá trị lọc không có trong dữ liệu" (tương ứng bản vá ReplacePredicateValue trong kế hoạch).

Tình huống: câu SQL đúng cú pháp, đúng bảng/cột, chạy được nhưng RA RỖNG, vì giá trị dùng để lọc
không khớp cách dữ liệu được lưu. Ví dụ:  CharterNum = '40'   nhưng dữ liệu ghi  '0040'.

Cách làm cho từng phép lọc dạng  cột = giá_trị  hoặc  cột IN (giá_trị, ...):
  1. Giá trị có trong cột không? Có -> bỏ qua (không phải lỗi giá trị).
  2. Không có -> tìm giá trị THẬT trong cột gần giống nhất:
       mức 1: chỉ khác cách viết (hoa/thường, khoảng trắng, dấu câu, số 0 ở đầu)
       mức 2: gõ sai nhẹ (≤ ~2 lần sửa ký tự) — chỉ với giá trị chữ, đủ dài
  3. Có đúng MỘT ứng viên rõ ràng -> đề xuất sửa. Hai ứng viên ngang nhau -> không đề xuất (từ chối).

Nguyên tắc: giá trị mới luôn lấy từ chính database (có bằng chứng), không tự bịa ra.
Cột có quá nhiều giá trị khác nhau (VD mã định danh, thời gian) thì bỏ qua để tránh khớp nhầm.
"""

import json
import re
import sqlite3
import threading
from typing import List, Optional

from sqlglot import exp

from vasp_sql.ast.joins import alias_to_table, from_items
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.diagnosis.engine import DiagnosedFault, FaultCategory
from vasp_sql.schema_graph import matching

MAX_DISTINCT = 20000   # cột có nhiều giá trị khác nhau hơn mức này thì bỏ qua
QUERY_TIMEOUT = 5.0    # giây, cho mỗi truy vấn tra cứu giá trị


def _norm(v) -> str:
    """Chuẩn hoá để so 'cách viết': chữ thường, bỏ ký tự không phải chữ/số, bỏ số 0 ở đầu chuỗi số."""
    s = re.sub(r"[^0-9a-z]", "", str(v).lower())
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s


def _q(name: str) -> str:
    """Đặt tên bảng/cột trong ngoặc kép cho SQLite."""
    return '"' + name.replace('"', '""') + '"'


class _DB:
    """Kết nối chỉ đọc, có giới hạn thời gian cho mỗi truy vấn."""

    def __init__(self, path):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)

    def query(self, sql, params=()):
        timer = threading.Timer(QUERY_TIMEOUT, self.conn.interrupt)
        timer.start()
        try:
            return self.conn.execute(sql, params).fetchall()
        finally:
            timer.cancel()

    def close(self):
        self.conn.close()


def _resolve(col: exp.Column, schema: SchemaMetadata):
    """Tìm (bảng thật, cột thật) mà cột trong câu SQL trỏ tới; None nếu không chắc."""
    sel = col.find_ancestor(exp.Select)
    if sel is None:
        return None
    amap = alias_to_table(sel)
    if col.table:
        tname = amap.get(col.table.lower())
        owners = [tname] if tname else []
    else:
        owners = [it.name.lower() for it in from_items(sel)
                  if isinstance(it, exp.Table) and schema.get_column(it.name, col.name)]
    owners = [t for t in dict.fromkeys(owners) if t and schema.get_column(t, col.name)]
    if len(owners) != 1:
        return None
    tbl = schema.get_table(owners[0])
    return tbl.name, tbl.get_column(col.name).name, tbl.get_column(col.name).data_type


def _literal_value(lit: exp.Literal):
    return lit.this if lit.is_string else lit.this  # dạng chuỗi như viết trong SQL


def _closest_value(lit: exp.Literal, values: list) -> tuple:
    """
    Trả về (giá_trị_tốt_nhất hoặc None, ghi_chú).
    """
    raw = _literal_value(lit)
    target = _norm(raw)
    # Mức 1: chỉ khác cách viết
    same = list(dict.fromkeys(v for v in values if _norm(v) == target))
    if len(same) == 1:
        return same[0], "same value, different formatting"
    if len(same) > 1:
        return None, f"ambiguous formatting match: {same[:3]}"
    # Mức 2: gõ sai nhẹ — chỉ với giá trị chữ, đủ dài để không khớp nhầm mã ngắn
    if not lit.is_string or len(target) < 4:
        return None, "no close value"
    texts = [(v, v) for v in values if isinstance(v, str) and abs(len(_norm(v)) - len(target)) <= 2]
    best, ambiguous, _ = matching.pick(raw, texts)
    if ambiguous:
        return None, "ambiguous typo match"
    if best is not None and best.distance <= matching.max_edits(raw):
        return best.item, f"typo match (edits={best.distance})"
    return None, "no close value"


def find_value_faults(ast: exp.Expression, schema: SchemaMetadata, db_path: str) -> List[DiagnosedFault]:
    """Tìm các giá trị lọc không có trong dữ liệu và đề xuất giá trị thật gần nhất."""
    literals = list(ast.find_all(exp.Literal))
    lit_pos = {id(l): i for i, l in enumerate(literals)}
    pairs = []  # (cột, literal)
    for node in ast.find_all(exp.EQ):
        a, b = node.this, node.expression
        if isinstance(a, exp.Literal) and isinstance(b, exp.Column):
            a, b = b, a
        if isinstance(a, exp.Column) and isinstance(b, exp.Literal):
            pairs.append((a, b))
    for node in ast.find_all(exp.In):
        if isinstance(node.this, exp.Column):
            for e in node.expressions or []:
                if isinstance(e, exp.Literal):
                    pairs.append((node.this, e))

    faults: List[DiagnosedFault] = []
    if not pairs:
        return faults
    db = _DB(db_path)
    distinct_cache = {}
    try:
        for col, lit in pairs:
            target = _resolve(col, schema)
            if target is None:
                continue
            table, column, _dtype = target
            lit_sql = lit.sql(dialect="sqlite")
            try:
                exists = db.query(f"SELECT 1 FROM {_q(table)} WHERE {_q(column)} = {lit_sql} LIMIT 1")
            except sqlite3.Error:
                continue
            if exists:
                continue  # giá trị có thật -> không phải lỗi giá trị
            key = (table, column)
            if key not in distinct_cache:
                try:
                    rows = db.query(f"SELECT DISTINCT {_q(column)} FROM {_q(table)} "
                                    f"WHERE {_q(column)} IS NOT NULL LIMIT {MAX_DISTINCT + 1}")
                    distinct_cache[key] = [r[0] for r in rows] if len(rows) <= MAX_DISTINCT else None
                except sqlite3.Error:
                    distinct_cache[key] = None
            values = distinct_cache[key]
            if not values:
                continue
            best, note = _closest_value(lit, values)
            if best is None:
                continue
            faults.append(DiagnosedFault(
                fault_type=FaultCategory.NO_SUCH_VALUE,
                target_element=lit_sql,
                table_context=table,
                column_name=column,
                literal_index=lit_pos[id(lit)],
                reason=f"Value {lit_sql} does not occur in {table}.{column}; the query returns no rows.",
                evidence=f"Closest value stored in the database: {best!r} ({note}).",
                confidence=0.9,
                suggested_replacements=[json.dumps(best, ensure_ascii=False)],
            ))
    finally:
        db.close()
    return faults
