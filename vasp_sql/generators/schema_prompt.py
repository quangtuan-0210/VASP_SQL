"""
Tạo phần mô tả schema đưa vào prompt cho các AI sinh SQL.

Dùng chung cho G1, G2, G3 để mọi AI nhận CÙNG thông tin schema (yêu cầu của kế hoạch mục 5.2).
Lấy nguyên câu CREATE TABLE trong database (có kiểu cột, khóa chính, khóa ngoại).
"""

import sqlite3
from functools import lru_cache


@lru_cache(maxsize=32)
def schema_ddl(db_path: str) -> str:
    """Toàn bộ câu CREATE TABLE của database (bỏ bảng nội bộ sqlite_*)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return "\n\n".join(r[0].strip() + ";" for r in rows)
