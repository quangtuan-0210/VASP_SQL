"""
VASP-SQL: Verified AST-localized SQL Repair
Lớp hậu xử lý sửa lỗi SQL do các AI agent sinh ra.
"""

__version__ = "0.1.0"

from vasp_sql.pipeline import repair

__all__ = ["repair", "__version__"]
