"""
Phân tích cú pháp SQL và chuyển AST ngược lại thành SQL, dùng sqlglot.
"""

from typing import Optional, Tuple
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from vasp_sql.core.feedback import ParserFeedback


class SQLParser:
    """
    Chuyển câu SQL thành AST và chuyển AST ngược lại thành SQL.
    Dialect mặc định là SQLite để tương thích với benchmark BIRD.
    """

    def __init__(self, default_dialect: str = "sqlite"):
        self.default_dialect = default_dialect

    def parse(self, sql: str, dialect: Optional[str] = None) -> exp.Expression:
        """
        Phân tích câu SQL thành AST (Expression của sqlglot).
        Báo lỗi ParseError nếu sai cú pháp.
        """
        active_dialect = dialect or self.default_dialect
        statements = sqlglot.parse(sql, read=active_dialect)
        if not statements or statements[0] is None:
            raise ParseError("SQL statement is empty or could not be parsed.")
        return statements[0]

    def try_parse(self, sql: str, dialect: Optional[str] = None) -> Tuple[Optional[exp.Expression], Optional[str]]:
        """
        Thử phân tích câu SQL mà không ném lỗi.
        Trả về (ast, None) nếu thành công, hoặc (None, thông báo lỗi) nếu thất bại.
        """
        try:
            tree = self.parse(sql, dialect)
            return tree, None
        except Exception as e:
            return None, str(e)

    def validate_syntax(self, sql: str, dialect: Optional[str] = None) -> ParserFeedback:
        """
        Gate A: kiểm tra câu truy vấn có đúng cú pháp của dialect không.
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
        Chuyển AST của sqlglot ngược lại thành chuỗi SQL gọn gàng.
        """
        active_dialect = dialect or self.default_dialect
        return ast.sql(dialect=active_dialect, pretty=pretty).strip()

    @staticmethod
    def calculate_ast_edit_size(original_ast: exp.Expression, repaired_ast: exp.Expression) -> int:
        """
        Tính khoảng cách sửa trên AST / số nút khác nhau giữa AST gốc và AST đã sửa.
        Dùng để đo tính cục bộ của bản sửa (sửa càng ít càng tốt).
        """
        orig_nodes = list(original_ast.walk())
        rep_nodes = list(repaired_ast.walk())

        orig_reprs = [type(n).__name__ + ":" + str(n.key) for n in orig_nodes]
        rep_reprs = [type(n).__name__ + ":" + str(n.key) for n in rep_nodes]

        # Hiệu đối xứng của hai tập (đa tập) để đếm số thay đổi cấu trúc
        from collections import Counter
        c_orig = Counter(orig_reprs)
        c_rep = Counter(rep_reprs)

        diff = (c_orig - c_rep) + (c_rep - c_orig)
        return sum(diff.values())

    @staticmethod
    def calculate_ast_nodes_changed(original_ast: exp.Expression, repaired_ast: exp.Expression) -> int:
        """
        Chỉ số BÁO CÁO tính cục bộ (mục 5.7): số nút AST bị thay / thêm / bớt.

        Khác calculate_ast_edit_size (chỉ so loại nút nên đổi tên cột a -> b bị tính 0), ở đây mỗi
        nút được nhận diện bằng loại + nội dung lá (tên định danh, giá trị hằng). Kết quả =
        max(số nút chỉ có ở bản gốc, số nút chỉ có ở bản sửa): thay 1 tên = 1, thêm điều kiện
        nối "a.x = b.y" = số nút của điều kiện đó.
        Lưu ý: KHÔNG dùng cho ngân sách sửa (planner vẫn dùng calculate_ast_edit_size như khi đóng băng).
        """
        from collections import Counter

        def sig(n):
            if isinstance(n, exp.Identifier):
                return "Identifier:" + str(n.name).lower()
            if isinstance(n, exp.Literal):
                return "Literal:" + str(n.this)
            return type(n).__name__

        c_orig = Counter(sig(n) for n in original_ast.walk())
        c_rep = Counter(sig(n) for n in repaired_ast.walk())
        return max(sum((c_orig - c_rep).values()), sum((c_rep - c_orig).values()))

    