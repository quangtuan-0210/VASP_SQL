"""
Gate F — kiểm tra ý nghĩa (intent) bằng LLM.

Hai việc:
  1. check():  câu SQL sau khi sửa có trả lời đúng câu hỏi không? -> "yes" / "no" / "unsure"
  2. choose(): khi có nhiều cách sửa hợp lệ nhưng cho kết quả khác nhau (VD nối superhero với
     colour qua màu mắt hay màu tóc), nhờ LLM chọn cách khớp với câu hỏi.

Nguyên tắc (theo kế hoạch nghiên cứu):
  - LLM KHÔNG tự viết bản sửa ở đây; các phương án đều do bộ sửa ký hiệu tạo ra và đã qua
    Gate A–E. LLM chỉ đóng vai "giám khảo" độc lập.
  - Chỉ dùng để LOẠI hoặc CHỌN giữa các phương án đã kiểm chứng, không dùng để chấp nhận
    một câu chưa qua các cổng khác.
  - Trả lời không đọc được -> coi như "unsure" (không chặn, không chọn bừa).
"""

import json
import re
from typing import List, Optional, Tuple

from sqlglot import exp

from vasp_sql.core.schema import SchemaMetadata

SYSTEM = (
    "You are a careful SQL reviewer for a Text-to-SQL benchmark (SQLite). "
    "You judge whether SQL queries answer a natural-language question, using the schema and hints given. "
    "Reply with JSON only."
)


def schema_snippet(schema: SchemaMetadata, sqls: List[str], max_cols: int = 40) -> str:
    """Rút gọn schema: chỉ lấy các bảng xuất hiện trong (các) câu SQL, kèm khóa ngoại giữa chúng."""
    import sqlglot

    used = set()
    for s in sqls:
        try:
            for t in sqlglot.parse_one(s, read="sqlite").find_all(exp.Table):
                if schema.has_table(t.name):
                    used.add(t.name.lower())
        except Exception:  # noqa: BLE001 — câu lỗi cú pháp thì bỏ qua
            continue
    lines = []
    for name in sorted(used):
        tbl = schema.get_table(name)
        cols = [f"{c.name} {c.data_type}" for c in list(tbl.columns.values())[:max_cols]]
        lines.append(f"TABLE {tbl.name} ({', '.join(cols)})")
    for fk in schema.foreign_keys:
        if fk.from_table.lower() in used and fk.to_table.lower() in used:
            lines.append(f"FK {fk.from_table}.{fk.from_column} -> {fk.to_table}.{fk.to_column}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    """Lấy object JSON đầu tiên trong câu trả lời (LLM đôi khi thêm chữ hoặc ```json)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


class IntentVerifier:
    def __init__(self, llm):
        self.llm = llm
        self.calls = 0  # số lần gọi LLM trong lần sửa hiện tại (để kiểm soát ngân sách)

    def reset(self):
        self.calls = 0

    # ---------------------------------------------------------------- 1. kiểm tra
    def check(self, question: str, evidence: Optional[str], schema: SchemaMetadata,
              original_sql: str, candidate_sql: str) -> Tuple[str, str]:
        """Trả về (verdict, lý do). verdict ∈ {"yes", "no", "unsure"}."""
        prompt = (
            f"Question: {question}\n"
            f"Hint: {evidence or '(none)'}\n\n"
            f"Schema:\n{schema_snippet(schema, [candidate_sql, original_sql])}\n\n"
            f"Original SQL (contained an error):\n{original_sql}\n\n"
            f"Repaired SQL:\n{candidate_sql}\n\n"
            "Does the repaired SQL correctly answer the question? Judge only meaning "
            "(tables, joins, columns, filters, aggregation), not style.\n"
            'Reply JSON: {"verdict": "yes" | "no" | "unsure", "reason": "<one sentence>"}'
        )
        self.calls += 1
        text = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                             max_tokens=200)
        data = _parse_json(text)
        verdict = str(data.get("verdict", "unsure")).strip().lower()
        if verdict not in ("yes", "no", "unsure"):
            verdict = "unsure"
        return verdict, str(data.get("reason", ""))[:300]

    # ---------------------------------------------------------------- 2. chọn phương án
    def choose(self, question: str, evidence: Optional[str], schema: SchemaMetadata,
               original_sql: str, options: List[str]) -> Tuple[Optional[int], str]:
        """Trả về (chỉ số phương án được chọn hoặc None nếu không chắc, lý do)."""
        listed = "\n\n".join(f"Option {i + 1}:\n{sql}" for i, sql in enumerate(options))
        prompt = (
            f"Question: {question}\n"
            f"Hint: {evidence or '(none)'}\n\n"
            f"Schema:\n{schema_snippet(schema, options)}\n\n"
            f"Original SQL (missing a join condition):\n{original_sql}\n\n"
            f"Candidate repairs (they differ only in how tables are joined):\n{listed}\n\n"
            "Which option answers the question? If none clearly fits, answer 0.\n"
            'Reply JSON: {"option": <number>, "reason": "<one sentence>"}'
        )
        self.calls += 1
        text = self.llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                             max_tokens=200)
        data = _parse_json(text)
        try:
            k = int(data.get("option", 0))
        except (TypeError, ValueError):
            k = 0
        reason = str(data.get("reason", ""))[:300]
        if 1 <= k <= len(options):
            return k - 1, reason
        return None, reason
