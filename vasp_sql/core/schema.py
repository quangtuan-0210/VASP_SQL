"""
Database Schema Representation and Metadata for VASP-SQL.
"""

from enum import Enum
from typing import Dict, List, Optional
import sqlite3
from pydantic import BaseModel, Field


class DataType(str, Enum):
    INTEGER = "INTEGER"
    REAL = "REAL"
    TEXT = "TEXT"
    BLOB = "BLOB"
    BOOLEAN = "BOOLEAN"
    UNKNOWN = "UNKNOWN"


class ColumnMetadata(BaseModel):
    name: str = Field(description="Column name.")
    data_type: str = Field(default="TEXT", description="Column data type (e.g. INTEGER, TEXT, REAL).")
    is_primary_key: bool = Field(default=False, description="Whether this column is part of the primary key.")
    is_nullable: bool = Field(default=True, description="Whether NULL values are permitted.")
    description: Optional[str] = Field(default=None, description="Optional description/comment for the column.")


class TableMetadata(BaseModel):
    name: str = Field(description="Table name.")
    columns: Dict[str, ColumnMetadata] = Field(
        default_factory=dict,
        description="Map of column name (lowercase) to ColumnMetadata."
    )
    primary_keys: List[str] = Field(
        default_factory=list,
        description="List of primary key column names."
    )

    def add_column(self, col: ColumnMetadata) -> None:
        self.columns[col.name.lower()] = col
        if col.is_primary_key and col.name.lower() not in [pk.lower() for pk in self.primary_keys]:
            self.primary_keys.append(col.name)

    def get_column(self, column_name: str) -> Optional[ColumnMetadata]:
        return self.columns.get(column_name.lower())

    def has_column(self, column_name: str) -> bool:
        return column_name.lower() in self.columns


class ForeignKeyRelation(BaseModel):
    from_table: str = Field(description="Source table name.")
    from_column: str = Field(description="Source column name.")
    to_table: str = Field(description="Target referenced table name.")
    to_column: str = Field(description="Target referenced column name.")
    name: Optional[str] = Field(default=None, description="Constraint name if available.")


class SchemaMetadata(BaseModel):
    db_id: str = Field(description="Database identifier.")
    dialect: str = Field(default="sqlite", description="Database SQL dialect.")
    tables: Dict[str, TableMetadata] = Field(
        default_factory=dict,
        description="Map of table name (lowercase) to TableMetadata."
    )
    foreign_keys: List[ForeignKeyRelation] = Field(
        default_factory=list,
        description="List of foreign key relationships between tables."
    )

    def add_table(self, table: TableMetadata) -> None:
        self.tables[table.name.lower()] = table

    def get_table(self, table_name: str) -> Optional[TableMetadata]:
        return self.tables.get(table_name.lower())

    def has_table(self, table_name: str) -> bool:
        return table_name.lower() in self.tables

    def get_column(self, table_name: str, column_name: str) -> Optional[ColumnMetadata]:
        tbl = self.get_table(table_name)
        if tbl:
            return tbl.get_column(column_name)
        return None

    def find_tables_with_column(self, column_name: str) -> List[str]:
        col_lower = column_name.lower()
        return [tbl.name for tbl in self.tables.values() if col_lower in tbl.columns]

    @classmethod
    def from_sqlite_db(cls, db_path: str, db_id: Optional[str] = None) -> "SchemaMetadata":
        """
        Introspect an active SQLite database file to construct SchemaMetadata automatically.
        """
        import os
        actual_db_id = db_id or os.path.splitext(os.path.basename(db_path))[0]
        schema = cls(db_id=actual_db_id, dialect="sqlite")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()

        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cursor.fetchall()]

        for tbl_name in tables:
            tbl_meta = TableMetadata(name=tbl_name)
            # Table info: cid, name, type, notnull, dflt_value, pk
            cursor.execute(f"PRAGMA table_info('{tbl_name}');")
            for row in cursor.fetchall():
                col_name = row[1]
                col_type = row[2] or "TEXT"
                notnull = bool(row[3])
                is_pk = bool(row[5])

                col_meta = ColumnMetadata(
                    name=col_name,
                    data_type=col_type.upper(),
                    is_primary_key=is_pk,
                    is_nullable=not notnull
                )
                tbl_meta.add_column(col_meta)

            schema.add_table(tbl_meta)

            # Foreign keys: id, seq, table, from, to, on_update, on_delete, match
            cursor.execute(f"PRAGMA foreign_key_list('{tbl_name}');")
            for fk in cursor.fetchall():
                target_table = fk[2]
                from_col = fk[3]
                to_col = fk[4]
                if from_col and to_col:
                    schema.foreign_keys.append(
                        ForeignKeyRelation(
                            from_table=tbl_name,
                            from_column=from_col,
                            to_table=target_table,
                            to_column=to_col
                        )
                    )

        conn.close()
        return schema
