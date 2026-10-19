"""
Bản vá "bỏ cột thừa" (DropSelectColumn) — hướng 1 mở rộng.

Lỗi nhắm tới: câu SQL chạy được nhưng SELECT trả về THÊM cột mà câu hỏi không hỏi
(VD hỏi "tên người cao nhất" mà trả về cả tên lẫn chiều cao). BIRD so khớp từng dòng kết quả,
nên thừa một cột là sai dù cột đúng có trong đó.

Nguyên tắc (giữ đúng tinh thần VASP-SQL):
  - Bản vá CHỈ ĐƯỢC BỚT cột ở SELECT ngoài cùng, không thêm, không đổi cột, không đụng phần khác.
  - LLM chỉ CHỌN giữ những cột nào trong danh sách đang có; không viết SQL.
  - Kiểm chứng sau khi bớt: câu mới phải qua Gate A–E và giữ nguyên số dòng
    (bớt cột không được làm thay đổi các dòng; câu có DISTINCT thì được phép ít dòng hơn).
  - LLM không chắc, chọn giữ hết, hoặc trả lời không đọc được -> giữ nguyên câu.
"""

from typing import List, Optional, Tuple

from sqlglot import exp

from vasp_sql.verifiers.intent import SYSTEM, _parse_json, schema_snippet


def top_select(ast: exp.Expression) -> Optional[exp.Select]:
    """SELECT ngoài cùng (bỏ qua câu UNION/INTERSECT/EXCEPT và câu không phải SELECT)."""
    node = ast
    if isinstance(node, exp.Subquery):
        node = node.this
    return node if isinstance(node, exp.Select) else None


def output_columns(ast: exp.Expression) -> List[exp.Expression]:
    """Các biểu thức đầu ra của SELECT ngoài cùng. Rỗng nếu không áp dụng được (có *, chỉ 1 cột...)."""
    sel = top_select(ast)
    if sel is None:
        return []
    exprs = list(sel.expressions)
    if len(exprs) < 2 or any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
                             for e in exprs):
        return []
    return exprs


def prune_projection(ast: exp.Expression, keep: List[int]) -> Optional[exp.Expression]:
    """
    Trả về AST mới chỉ giữ các cột đầu ra có chỉ số trong keep (giữ thứ tự gốc).
    Cột bị bỏ mà có bí danh đang được ORDER BY / GROUP BY / HAVING dùng tới -> thay bí danh bằng
    chính biểu thức đó, để câu vẫn chạy đúng như cũ. ORDER BY theo số thứ tự cột -> đổi số cho khớp.
    """
    new_ast = ast.copy()
    sel = top_select(new_ast)
    if sel is None:
        return None
    exprs = list(sel.expressions)
    if not keep or len(keep) >= len(exprs) or any(i < 0 or i >= len(exprs) for i in keep):
        return None
    keep = sorted(set(keep))
    dropped = [exprs[i] for i in range(len(exprs)) if i not in keep]
    alias_map = {e.alias.lower(): e.unalias().copy() for e in dropped if isinstance(e, exp.Alias) and e.alias}

    for part in ("order", "group", "having"):
        node = sel.args.get(part)
        if node is None:
            continue
        # ORDER BY 2 -> biểu thức của cột thứ 2 (vì thứ tự cột sẽ thay đổi)
        if part == "order":
            for od in node.expressions:
                if isinstance(od.this, exp.Literal) and od.this.is_int:
                    pos = int(od.this.this) - 1
                    if 0 <= pos < len(exprs):
                        od.set("this", exprs[pos].unalias().copy())
        if alias_map:
            for col in list(node.find_all(exp.Column)):
                if not col.table and col.name.lower() in alias_map:
                    col.replace(alias_map[col.name.lower()].copy())

    sel.set("expressions", [exprs[i] for i in keep])
    return new_ast


def describe_columns(exprs: List[exp.Expression], sample_row: Optional[list]) -> str:
    lines = []
    for i, e in enumerate(exprs):
        val = ""
        if sample_row is not None and i < len(sample_row):
            v = sample_row[i]
            val = f"   (example value: {str(v)[:60]!r})"
        lines.append(f"{i + 1}. {e.sql(dialect='sqlite')[:160]}{val}")
    return "\n".join(lines)


class ProjectionSelector:
    """Hỏi LLM: câu hỏi yêu cầu trả về những cột nào trong danh sách? (chỉ được chọn, không viết SQL)"""

    def __init__(self, llm):
        self.llm = llm
        self.calls = 0
        self.log: List[dict] = []

    def select(self, question: str, evidence: Optional[str], schema, sql: str,
               exprs: List[exp.Expression], sample_row: Optional[list]) -> Tuple[Optional[List[int]], str]:
        prompt = (
            f"Question: {question}\n"
            f"Hint: {evidence or '(none)'}\n\n"
            f"Schema:\n{schema_snippet(schema, [sql])}\n\n"
            f"SQL:\n{sql}\n\n"
            f"The query returns these output columns, in order:\n{describe_columns(exprs, sample_row)}\n\n"
            "The answer is graded by comparing the returned rows with a reference answer, so it must contain "
            "exactly the information the question asks for — no extra columns.\n"
            "Which output columns does the question explicitly ask to be returned? "
            "A column that is only used to sort, rank, filter or compute something is NOT requested unless the "
            "question also asks to show it. If the question asks for several pieces of information, keep all of them. "
            "If every column is requested, keep all.\n"
            'Reply JSON: {"keep": [<column numbers>], "confident": true | false, "reason": "<one sentence>"}'
        )
        self.calls += 1
        text = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                             max_tokens=600)
        data = _parse_json(text)
        reason = str(data.get("reason", ""))[:300]
        try:
            keep = sorted({int(k) - 1 for k in data.get("keep", [])})
        except (TypeError, ValueError):
            keep = []
        confident = data.get("confident") in (True, "true", "yes")
        self.log.append({"type": "projection", "keep": [k + 1 for k in keep], "confident": confident,
                         "reason": reason, "n_cols": len(exprs)})
        if not confident or not keep or len(keep) >= len(exprs) or any(k < 0 or k >= len(exprs) for k in keep):
            return None, reason
        return keep, reason
