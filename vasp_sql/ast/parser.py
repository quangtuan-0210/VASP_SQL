"""
SQL Parser and AST Serializer using sqlglot.
"""

from typing import Optional, Tuple
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from vasp_sql.core.feedback import ParserFeedback


class SQLParser:
    """
    Parser for SQL statements to AST and serializer from AST to SQL.
    Default dialect is SQLite for BIRD benchmark compatibility.
    """

    def __init__(self, default_dialect: str = "sqlite"):
        self.default_dialect = default_dialect

    def parse(self, sql: str, dialect: Optional[str] = None) -> exp.Expression:
        """
        Parse SQL query into a sqlglot Expression AST.
        Raises ParseError if syntax is invalid.
        """
        active_dialect = dialect or self.default_dialect
        statements = sqlglot.parse(sql, read=active_dialect)
        if not statements or statements[0] is None:
            raise ParseError("SQL statement is empty or could not be parsed.")
        return statements[0]

    def try_parse(self, sql: str, dialect: Optional[str] = None) -> Tuple[Optional[exp.Expression], Optional[str]]:
        """
        Safely try to parse SQL query without throwing exceptions.
        Returns (ast, None) on success or (None, error_message) on failure.
        """
        try:
            tree = self.parse(sql, dialect)
            return tree, None
        except Exception as e:
            return None, str(e)

    def validate_syntax(self, sql: str, dialect: Optional[str] = None) -> ParserFeedback:
        """
        Gate A: Check whether the query conforms to valid dialect syntax.
        """
        active_dialect = dialect or self.default_dialect
        try:
            self.parse(sql, dialect=active_dialect)
            return ParserFeedback(is_valid_syntax=True)
        except ParseError as pe:
            return ParserFeedback(
                is_valid_syntax=False,
                error_message=str(pe),
                line=getattr(pe, "line", None),
                col=getattr(pe, "col", None)
            )
        except Exception as e:
            return ParserFeedback(
                is_valid_syntax=False,
                error_message=str(e)
            )

    def to_sql(
        self,
        ast: exp.Expression,
        dialect: Optional[str] = None,
        pretty: bool = False
    ) -> str:
        """
        Serialize sqlglot AST back to clean SQL string.
        """
        active_dialect = dialect or self.default_dialect
        return ast.sql(dialect=active_dialect, pretty=pretty).strip()

    @staticmethod
    def calculate_ast_edit_size(original_ast: exp.Expression, repaired_ast: exp.Expression) -> int:
        """
        Calculate the AST edit distance / number of differing nodes between original and repaired ASTs.
        Used to measure AST locality (ensuring minimum changes).
        """
        orig_nodes = list(original_ast.walk())
        rep_nodes = list(repaired_ast.walk())

        orig_reprs = [type(n).__name__ + ":" + str(n.key) for n in orig_nodes]
        rep_reprs = [type(n).__name__ + ":" + str(n.key) for n in rep_nodes]

        # Simple multiset symmetric difference to count structural changes
        from collections import Counter
        c_orig = Counter(orig_reprs)
        c_rep = Counter(rep_reprs)

        diff = (c_orig - c_rep) + (c_rep - c_orig)
        return sum(diff.values())

    