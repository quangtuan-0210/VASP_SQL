"""
VASP-SQL: Verified AST-localized SQL Repair
A post-processing repair layer for SQL-generating agents.
"""

__version__ = "0.1.0"

from vasp_sql.pipeline import repair

__all__ = ["repair", "__version__"]
