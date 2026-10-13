"""
Sửa lỗi Ý NGHĨA cục bộ bằng LLM (hướng 2) — tương ứng "LLM chỉ được sinh patch có cấu trúc hoặc
sửa vùng AST cụ thể" và "LLM không tự xác nhận phương án của chính nó" trong kế hoạch (mục 3.5).

Dùng cho câu đã qua Gate A–E (đúng cú pháp, đúng schema, chạy được) nhưng có thể sai ý câu hỏi.

Quy trình (tối đa 2 lượt gọi LLM):
  1. ĐỀ XUẤT (lượt 1): LLM xem câu hỏi, gợi ý, schema, câu SQL và vài dòng kết quả, rồi trả lời
     câu SQL đúng hay sai; nếu sai, chỉ được đưa ra TỐI ĐA 2 thao tác sửa trong danh sách cố định:
       replace_expression  : thay một biểu thức có sẵn (cột, phép tính, điều kiện, mục ORDER BY)
       add_condition       : thêm một điều kiện (AND) vào WHERE
       remove_condition    : bỏ một điều kiện khỏi WHERE
       (replace_select_list : thay danh sách cột SELECT — ĐÃ TẮT: ở lần thử đầu trên phần tune,
        thao tác này chỉ gây hỏng câu đúng, VD đổi kết quả thành 'Yes'/'No', không sửa được câu nào)
       set_limit           : đổi LIMIT / OFFSET
       set_distinct        : bật / tắt DISTINCT
     LLM KHÔNG viết lại cả câu. Hệ thống tự áp thao tác lên đúng nút trong AST (phần còn lại giữ nguyên).
  2. Câu sau khi sửa phải qua lại Gate A–E (làm ở pipeline).
  3. KIỂM CHỨNG ĐỘC LẬP (lượt 2): một lời gọi tới MÔ HÌNH KHÁC (mặc định gpt-oss-120b, xem VT_MODEL_JUDGE),
     không thấy lý do của bước 1, được xem hai
     câu (thứ tự xáo theo mã câu hỏi để tránh thiên vị vị trí) kèm kết quả chạy, và chọn câu trả lời
     đúng câu hỏi hơn. Chỉ nhận bản sửa nếu nó được chọn; "không chắc" -> giữ nguyên câu gốc.
"""

import hashlib
import json
import re
from typing import List, Optional, Tuple

import sqlglot
from sqlglot import exp

from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.verifiers.intent import SYSTEM, _parse_json, schema_snippet

ALLOWED_OPS = {"replace_expression", "add_condition", "remove_condition", "set_limit", "set_distinct"}
MAX_EDITS = 2

PROPOSE_PROMPT = """Question: {question}
Hint: {evidence}

Schema:
{schema}

SQL written by another system:
{sql}

Its result: {n_rows} row(s). First rows: {preview}

Does this SQL correctly answer the question? Check carefully: the returned columns (exactly what is asked,
nothing missing), aggregation (COUNT vs COUNT DISTINCT, SUM vs AVG), ordering direction, filter conditions and
values, LIMIT, DISTINCT, ratio / percentage formulas, and use of the hint.

If it is correct (or you are not sure), answer {{"correct": true}}.
If it is wrong, DO NOT rewrite the query. Give at most 2 small edits, each one of:
  {{"op": "replace_expression", "old": "<exact expression copied from the SQL>", "new": "<new expression>"}}
  {{"op": "add_condition", "new": "<condition to AND into WHERE>"}}
  {{"op": "remove_condition", "old": "<exact condition copied from the WHERE clause>"}}
  {{"op": "set_limit", "limit": <int or null>, "offset": <int or null>}}
  {{"op": "set_distinct", "value": true|false}}
Use the table aliases already in the SQL.
Reply JSON only: {{"correct": false, "edits": [...], "reason": "<one sentence>"}}"""

VERIFY_PROMPT = """Question: {question}
Hint: {evidence}

Schema:
{schema}

Two candidate SQL queries:

Option 1:
{sql1}
Result: {n1} row(s). First rows: {p1}

Option 2:
{sql2}
Result: {n2} row(s). First rows: {p2}

Which option answers the question correctly? Consider returned columns, aggregation, filters, ordering and the hint.
If both are equally good or you cannot tell, answer 0.
Reply JSON only: {{"option": 0 | 1 | 2, "reason": "<one sentence>"}}"""


def _norm(s: str) -> str:
    return re.sub(r"[\s`\"'\[\]]", "", s or "").lower().rstrip(";")


def _expr(text: str) -> Optional[exp.Expression]:
    """Phân tích một biểu thức SQL rời (qua 'SELECT <biểu thức>')."""
    try:
        sel = sqlglot.parse_one(f"SELECT {text}", read="sqlite")
        return sel.expressions[0] if sel.expressions else None
    except Exception:  # noqa: BLE001
        return None


def _top_select(ast: exp.Expression) -> Optional[exp.Select]:
    return ast if isinstance(ast, exp.Select) else None


def _find_node(ast: exp.Expression, text: str) -> Optional[exp.Expression]:
    """Nút NHỎ NHẤT trong AST có nội dung trùng với `text` (bỏ qua khoảng trắng / dấu ngoặc kép)."""
    target = _norm(text)
    best = None
    for node in ast.walk():
        if isinstance(node, (exp.Select, exp.Identifier)):
            continue
        try:
            s = _norm(node.sql(dialect="sqlite"))
        except Exception:  # noqa: BLE001
            continue
        if s == target:
            if best is None or len(list(node.walk())) < len(list(best.walk())):
                best = node
    return best


def apply_edits(ast: exp.Expression, edits: List[dict]) -> Tuple[Optional[exp.Expression], List[str]]:
    """Áp các thao tác lên bản sao của AST. Trả về (AST mới hoặc None nếu thao tác không hợp lệ, mô tả)."""
    tree = ast.copy()
    sel = _top_select(tree)
    if sel is None:
        return None, ["top-level statement is not a simple SELECT"]
    notes = []
    for e in edits[:MAX_EDITS]:
        op = e.get("op")
        if op not in ALLOWED_OPS:
            return None, [f"op not allowed: {op}"]
        if op == "replace_expression":
            node, new = _find_node(tree, str(e.get("old", ""))), _expr(str(e.get("new", "")))
            if node is None or new is None:
                return None, [f"cannot locate/parse expression: {e.get('old')!r} -> {e.get('new')!r}"]
            if isinstance(new, exp.Alias) and not isinstance(node, exp.Alias):
                new = new.this
            node.replace(new)
            notes.append(f"replace_expression: {e.get('old')} -> {e.get('new')}")
        elif op == "add_condition":
            cond = _expr(str(e.get("new", "")))
            if cond is None:
                return None, [f"cannot parse condition {e.get('new')!r}"]
            where = sel.args.get("where")
            sel.set("where", exp.Where(this=exp.and_(where.this, cond) if where else cond))
            notes.append(f"add_condition: {e.get('new')}")
        elif op == "remove_condition":
            where = sel.args.get("where")
            node = _find_node(where, str(e.get("old", ""))) if where else None
            if node is None:
                return None, [f"cannot locate condition {e.get('old')!r}"]
            parent = node.parent
            if isinstance(parent, exp.And):
                other = parent.expression if parent.this is node else parent.this
                parent.replace(other)
            elif isinstance(parent, exp.Where):
                sel.set("where", None)
            else:
                return None, ["condition is not a top-level conjunct of WHERE"]
            notes.append(f"remove_condition: {e.get('old')}")
        elif op == "replace_select_list":
            try:
                new_sel = sqlglot.parse_one(f"SELECT {e.get('new', '')}", read="sqlite")
            except Exception:  # noqa: BLE001
                return None, [f"cannot parse select list {e.get('new')!r}"]
            sel.set("expressions", new_sel.expressions)
            notes.append(f"replace_select_list: {e.get('new')}")
        elif op == "set_limit":
            lim, off = e.get("limit"), e.get("offset")
            sel.set("limit", exp.Limit(expression=exp.Literal.number(int(lim))) if lim is not None else None)
            sel.set("offset", exp.Offset(expression=exp.Literal.number(int(off))) if off else None)
            notes.append(f"set_limit: limit={lim} offset={off}")
        elif op == "set_distinct":
            sel.set("distinct", exp.Distinct() if e.get("value") else None)
            notes.append(f"set_distinct: {bool(e.get('value'))}")
    return tree, notes


def _preview(feed) -> Tuple[int, str]:
    rows = (feed.result_preview or [])[:5] if feed else []
    return (feed.row_count or 0) if feed else 0, json.dumps(rows, ensure_ascii=False, default=str)[:600]


class SemanticRepairer:
    def __init__(self, llm, judge_llm=None):
        self.llm = llm
        # Giám khảo: nên là MÔ HÌNH KHÁC với mô hình đề xuất. Lần thử đầu dùng cùng Qwen3-235B cho cả hai:
        # giám khảo đồng ý 37/48 bản sửa, kể cả bản làm hỏng câu đúng (cùng mô hình -> dễ đồng tình với nhau).
        self.judge = judge_llm or llm
        self.calls = 0
        self.log = []

    def propose(self, question, evidence, schema: SchemaMetadata, sql, feed) -> List[dict]:
        n, prev = _preview(feed)
        prompt = PROPOSE_PROMPT.format(question=question, evidence=evidence or "(none)",
                                       schema=schema_snippet(schema, [sql]), sql=sql, n_rows=n, preview=prev)
        self.calls += 1
        data = _parse_json(self.llm.chat([{"role": "system", "content": SYSTEM},
                                          {"role": "user", "content": prompt}], max_tokens=500))
        edits = data.get("edits") or []
        correct = data.get("correct", True)
        self.log.append({"type": "propose", "correct": correct, "edits": edits, "reason": str(data.get("reason", ""))[:300]})
        if correct is True or str(correct).lower() == "true" or not isinstance(edits, list):
            return []
        return [e for e in edits if isinstance(e, dict)][:MAX_EDITS]

    def verify(self, qid_key: str, question, evidence, schema, sql_orig, feed_orig, sql_new, feed_new,
               force_flip=None) -> bool:
        """Lời gọi độc lập: chọn giữa câu gốc và câu đã sửa (thứ tự xáo theo mã câu hỏi).
        force_flip: ép thứ tự (dùng khi chấm lần 2 với vị trí đảo ngược)."""
        flip = int(hashlib.md5(qid_key.encode()).hexdigest(), 16) % 2 == 1
        if force_flip is not None:
            flip = force_flip
        a, fa, b, fb = (sql_new, feed_new, sql_orig, feed_orig) if flip else (sql_orig, feed_orig, sql_new, feed_new)
        n1, p1 = _preview(fa)
        n2, p2 = _preview(fb)
        prompt = VERIFY_PROMPT.format(question=question, evidence=evidence or "(none)",
                                      schema=schema_snippet(schema, [sql_orig, sql_new]),
                                      sql1=a, n1=n1, p1=p1, sql2=b, n2=n2, p2=p2)
        self.calls += 1
        data = _parse_json(self.judge.chat([{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": prompt}], max_tokens=1500))
        try:
            pick = int(data.get("option", 0))
        except (TypeError, ValueError):
            pick = 0
        new_pos = 1 if flip else 2
        ok = pick == new_pos
        self.log.append({"type": "verify", "picked_repair": ok, "pick": pick, "repair_position": new_pos,
                         "reason": str(data.get("reason", ""))[:300]})
        return ok

    def verify_both_orders(self, qid_key: str, question, evidence, schema, sql_orig, feed_orig,
                           sql_new, feed_new) -> bool:
        """
        Giám khảo chấm 2 lần với vị trí 2 phương án ĐẢO NGƯỢC; chỉ nhận bản sửa khi cả 2 lần
        đều chọn nó. Giảm thiên vị vị trí (luôn thích "Option 1" hoặc "Option 2").
        Lần 1 giống hệt verify() thường, nên dùng lại được bộ nhớ đệm.
        """
        if not self.verify(qid_key, question, evidence, schema, sql_orig, feed_orig, sql_new, feed_new):
            return False
        flip = int(hashlib.md5(qid_key.encode()).hexdigest(), 16) % 2 == 1
        return self.verify(qid_key, question, evidence, schema, sql_orig, feed_orig, sql_new, feed_new,
                           force_flip=not flip)
