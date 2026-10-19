"""
Pipeline sửa lỗi lõi của VASP-SQL: điều phối chẩn đoán, vá và kiểm chứng.
"""

import time
from typing import Optional

from vasp_sql.ast.parser import SQLParser
from vasp_sql.ast.scope import ScopeAnalyzer
from vasp_sql.core.feedback import ExecutionFeedback
from vasp_sql.core.policy import RepairPolicy
from vasp_sql.core.result import GateResult, GateType, RepairResult, RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.diagnosis.engine import DiagnosisEngine, DiagnosticReport
from vasp_sql.diagnosis.engine import FaultCategory
from vasp_sql.diagnosis.values import find_value_faults
from vasp_sql.repair.planner import RepairPlanner
from vasp_sql.repair.projection import ProjectionSelector, output_columns, prune_projection, top_select
from vasp_sql.repair.semantic import SemanticRepairer, _norm as _norm_sql, apply_edits
from vasp_sql.core.result import ASTPatchRecord
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor
from vasp_sql.schema_graph.graph import SchemaKnowledgeGraph
from vasp_sql.verifiers.gates import VerificationPipeline
from vasp_sql.verifiers.intent import IntentVerifier


def _semantic_attempt(sem, question, evidence, schema, sql, original_ast, orig_feed, verifiers, sandbox,
                      parser, dialect, llm_budget, start_time, base_gates):
    """
    Hướng 2: thử sửa lỗi ý nghĩa cục bộ cho câu đã qua Gate A–E. Trả về RepairResult (REPAIRED) hoặc None.
    """
    edits = sem.propose(question, evidence, schema, sql, orig_feed)
    if not edits:
        return None
    new_ast, notes = apply_edits(original_ast, edits)
    if new_ast is None:
        sem.log.append({"type": "apply_failed", "notes": notes})
        return None
    new_sql = parser.to_sql(new_ast, dialect=dialect)
    if _norm_sql(new_sql) == _norm_sql(sql):
        return None
    gates = [verifiers.verify_gate_a_syntax(new_sql, dialect=dialect)]
    if gates[-1].passed:
        for g in (verifiers.verify_gate_b_schema(new_ast, schema, dialect=dialect),
                  verifiers.verify_gate_c_type(new_ast, schema),
                  verifiers.verify_gate_d_safety(new_sql, sandbox)):
            gates.append(g)
            if not g.passed:
                break
    if not all(g.passed for g in gates):
        sem.log.append({"type": "gates_failed", "sql": new_sql[:300], "gate": gates[-1].gate.value})
        return None
    g_e, new_feed = verifiers.verify_gate_e_execution(new_sql, sandbox)
    if not g_e.passed:
        sem.log.append({"type": "gates_failed", "sql": new_sql[:300], "gate": "GATE_E_EXECUTION"})
        return None
    if sem.calls >= llm_budget:
        sem.log.append({"type": "budget_exhausted"})
        return None
    if not sem.verify(question, question, evidence, schema, sql, orig_feed, new_sql, new_feed):
        return None
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    return RepairResult(
        status=RepairStatus.REPAIRED,
        final_sql=new_sql,
        original_sql=sql,
        patches_applied=[ASTPatchRecord(patch_type="SemanticEdit", original_fragment=sql[:300],
                                        replacement_fragment="; ".join(notes)[:500],
                                        reason="LLM-proposed localized edit (semantic error)",
                                        evidence="Accepted by an independent LLM comparison of original vs repaired query")],
        gate_results=gates + [g_e, GateResult(gate=GateType.GATE_F_INTENT, passed=True, confidence=0.7,
                                              message="Independent LLM judge preferred the repaired query.")],
        confidence=0.7,
        metrics={"latency_ms": round(elapsed_ms, 2), "llm_calls": sem.calls, "intent_log": sem.log,
                 "ast_edit_size": parser.calculate_ast_edit_size(original_ast, new_ast),
                 "ast_nodes_changed": parser.calculate_ast_nodes_changed(original_ast, new_ast)},
    )


def _projection_attempt(selector, question, evidence, schema, sql, original_ast, orig_feed, verifiers,
                        sandbox, parser, dialect, start_time):
    """
    Bản vá bỏ cột thừa cho câu đã qua Gate A–E và có kết quả. Trả về RepairResult (REPAIRED) hoặc None.
    LLM chỉ chọn giữ cột nào; hệ thống tự bớt cột trên AST rồi kiểm chứng lại.
    """
    exprs = output_columns(original_ast)
    if not exprs or not (orig_feed.row_count or 0) > 0:
        return None
    sample = (orig_feed.result_preview or [None])[0]
    keep, why = selector.select(question, evidence, schema, sql, exprs, sample)
    if keep is None:
        return None
    new_ast = prune_projection(original_ast, keep)
    if new_ast is None:
        return None
    new_sql = parser.to_sql(new_ast, dialect=dialect)
    gates = [verifiers.verify_gate_a_syntax(new_sql, dialect=dialect)]
    if gates[-1].passed:
        for g in (verifiers.verify_gate_b_schema(new_ast, schema, dialect=dialect),
                  verifiers.verify_gate_c_type(new_ast, schema),
                  verifiers.verify_gate_d_safety(new_sql, sandbox)):
            gates.append(g)
            if not g.passed:
                break
    if not all(g.passed for g in gates):
        selector.log.append({"type": "projection_gates_failed", "gate": gates[-1].gate.value})
        return None
    g_e, new_feed = verifiers.verify_gate_e_execution(new_sql, sandbox)
    if not g_e.passed:
        selector.log.append({"type": "projection_gates_failed", "gate": "GATE_E_EXECUTION"})
        return None
    # Bớt cột không được làm đổi các dòng: số dòng phải giữ nguyên (câu có DISTINCT được phép ít hơn)
    sel = top_select(original_ast)
    has_distinct = sel is not None and sel.args.get("distinct") is not None
    if (new_feed.row_count or 0) == 0 or (not has_distinct and new_feed.row_count != orig_feed.row_count):
        selector.log.append({"type": "projection_rows_changed", "before": orig_feed.row_count,
                             "after": new_feed.row_count})
        return None
    dropped = [e.sql(dialect=dialect) for i, e in enumerate(exprs) if i not in keep]
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    return RepairResult(
        status=RepairStatus.REPAIRED,
        final_sql=new_sql,
        original_sql=sql,
        patches_applied=[ASTPatchRecord(patch_type="DropSelectColumn", original_fragment=", ".join(dropped)[:300],
                                        replacement_fragment="(removed)",
                                        reason="Output columns not requested by the question",
                                        evidence=f"LLM selection of requested columns: {why}"[:300])],
        gate_results=gates + [g_e, GateResult(gate=GateType.GATE_F_INTENT, passed=True, confidence=0.8,
                                              message=f"Kept columns {[k + 1 for k in keep]} of {len(exprs)}: {why}")],
        confidence=0.8,
        metrics={"latency_ms": round(elapsed_ms, 2), "llm_calls": selector.calls, "intent_log": selector.log,
                 "ast_edit_size": parser.calculate_ast_edit_size(original_ast, new_ast),
                 "ast_nodes_changed": parser.calculate_ast_nodes_changed(original_ast, new_ast)},
    )


def repair(
    question: str,
    sql: str,
    schema: SchemaMetadata,
    db_path: str,
    dialect: str = "sqlite",
    evidence: Optional[str] = None,
    execution_feedback: Optional[ExecutionFeedback] = None,
    policy: Optional[RepairPolicy] = None,
    llm=None,
    judge_llm=None
) -> RepairResult:
    """
    Điểm vào chính của lớp sửa lỗi VASP-SQL.
    Làm theo Thuật toán 1 trong thiết kế VASP-SQL:
    1. Kiểm tra câu gốc. Nếu qua mọi cổng -> trả về UNCHANGED (giữ nguyên).
    2. Nếu câu gốc lỗi -> chẩn đoán và định vị lỗi.
    3. Lập các bản vá cục bộ trên AST, trong giới hạn ngân sách (RepairBudget).
    4. Kiểm tra lần lượt từng ứng viên qua Gate A tới F.
    5. Trả về ứng viên xếp hạng cao nhất qua được mọi cổng -> REPAIRED.
    6. Nếu không ứng viên nào qua -> TỪ CHỐI (ABSTAIN) một cách an toàn.

    llm: (tuỳ chọn) một LLMClient. Khi có llm VÀ policy.use_llm_intent=True, Gate F dùng LLM để
         (a) loại bản sửa chạy được nhưng sai ý câu hỏi, (b) chọn giữa các cách nối bảng cho
         kết quả khác nhau. Không có llm thì Gate F chỉ kiểm tra đơn giản như trước.
    """
    start_time = time.perf_counter()
    active_policy = policy or RepairPolicy()

    parser = SQLParser(default_dialect=dialect)
    scope_analyzer = ScopeAnalyzer(parser)
    kg = SchemaKnowledgeGraph(schema)
    sandbox = SQLiteSandboxExecutor(db_path=db_path, policy=active_policy.safety)
    diagnosis_engine = DiagnosisEngine(parser, scope_analyzer)
    planner = RepairPlanner(parser)
    verifiers = VerificationPipeline(parser, scope_analyzer)
    # Gate F bằng LLM: chỉ bật khi có llm và policy cho phép
    intent = IntentVerifier(llm) if (llm is not None and active_policy.use_llm_intent) else None
    llm_budget = active_policy.budget.max_llm_calls
    intent_log = []  # ghi lại các lần LLM phán xét (để báo cáo / phân tích lỗi)
    # Hướng 2: sửa lỗi ý nghĩa cục bộ (chỉ khi có llm và policy bật)
    semantic = SemanticRepairer(llm, judge_llm) if (llm is not None and active_policy.allow_semantic_repair) else None
    # Bản vá bỏ cột thừa (cần llm để chọn cột cần giữ)
    projection = ProjectionSelector(llm) if (llm is not None and active_policy.allow_projection_repair) else None
    orig_feed = None  # kết quả chạy câu gốc (để LLM xem khi đề xuất / so sánh)

    def finish_unchanged():
        """Câu gốc hợp lệ: thử bỏ cột thừa / hướng 2 (nếu bật); không sửa được thì giữ nguyên câu."""
        if projection is not None and original_ast is not None and orig_feed is not None:
            res = _projection_attempt(projection, question, evidence, schema, sql, original_ast, orig_feed,
                                      verifiers, sandbox, parser, dialect, start_time)
            if res is not None:
                return res
            unchanged_result.metrics.update({"llm_calls": projection.calls, "intent_log": projection.log})
        if semantic is not None and original_ast is not None and orig_feed is not None:
            res = _semantic_attempt(semantic, question, evidence, schema, sql, original_ast, orig_feed,
                                    verifiers, sandbox, parser, dialect, llm_budget, start_time,
                                    unchanged_result.gate_results)
            if res is not None:
                return res
            unchanged_result.metrics.update({"llm_calls": semantic.calls, "intent_log": semantic.log})
        return unchanged_result

    # -------------------------------------------------------------
    # Bước 1: kiểm tra câu gốc đã hoàn toàn hợp lệ chưa
    # -------------------------------------------------------------
    gate_a = verifiers.verify_gate_a_syntax(sql, dialect=dialect)
    original_ast = None
    empty_mode = False        # True: câu gốc hợp lệ nhưng ra rỗng, đang thử sửa giá trị lọc
    unchanged_result = None   # kết quả "giữ nguyên" để trả về nếu không sửa được trong empty_mode

    if gate_a.passed:
        original_ast = parser.parse(sql, dialect=dialect)
        gate_b = verifiers.verify_gate_b_schema(original_ast, schema, dialect=dialect)
        gate_c = verifiers.verify_gate_c_type(original_ast, schema)
        gate_d = verifiers.verify_gate_d_safety(sql, sandbox)

        if gate_b.passed and gate_c.passed and gate_d.passed:
            gate_e, exec_feed = verifiers.verify_gate_e_execution(sql, sandbox)
            orig_feed = exec_feed
            gate_f = verifiers.verify_gate_f_intent(question, sql, evidence)

            if gate_e.passed and gate_f.passed:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                unchanged_result = RepairResult(
                    status=RepairStatus.UNCHANGED,
                    final_sql=sql,
                    original_sql=sql,
                    patches_applied=[],
                    gate_results=[gate_a, gate_b, gate_c, gate_d, gate_e, gate_f],
                    confidence=1.0,
                    metrics={"latency_ms": round(elapsed_ms, 2), "ast_edit_size": 0}
                )
                # Câu hợp lệ nhưng RA RỖNG: có thể giá trị lọc không khớp cách dữ liệu được lưu
                # (VD '40' thay vì '0040'). Thử tìm giá trị thật; không tìm được thì giữ nguyên câu.
                if active_policy.allow_value_repair and exec_feed.row_count == 0:
                    value_faults = find_value_faults(original_ast, schema, db_path)
                    if value_faults:
                        empty_mode = True
                        report = DiagnosticReport(has_faults=True, faults=value_faults)
                if not empty_mode:
                    return finish_unchanged()

    # -------------------------------------------------------------
    # Bước 2: câu có lỗi -> chẩn đoán nhiều tầng
    # -------------------------------------------------------------
    if not empty_mode:
        report = diagnosis_engine.diagnose(
            sql=sql,
            schema=schema,
            kg=kg,
            execution_feedback=execution_feedback,
            dialect=dialect
        )

    # AST-only: không dùng đồ thị schema -> bỏ các lỗi thiếu JOIN (chỉ đồ thị mới tìm được cách nối)
    if not active_policy.use_schema_graph:
        kept = [f for f in report.faults if f.fault_type != FaultCategory.MISSING_JOIN]
        report = DiagnosticReport(has_faults=bool(kept), faults=kept)

    if not report.has_faults or original_ast is None:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return RepairResult(
            status=RepairStatus.ABSTAIN,
            final_sql=sql,
            original_sql=sql,
            confidence=0.0,
            abstain_reason="Query syntax could not be recovered or no definitive diagnostic faults found.",
            metrics={"latency_ms": round(elapsed_ms, 2)}
        )

    # -------------------------------------------------------------
    # Bước 3: lập các bản vá ứng viên trên AST, trong giới hạn ngân sách
    # -------------------------------------------------------------
    candidates = planner.plan_candidates(
        original_ast=original_ast,
        report=report,
        schema=schema,
        kg=kg,
        budget=active_policy.budget,
        dialect=dialect
    )

    if not candidates and empty_mode:
        return finish_unchanged()
    if not candidates:
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return RepairResult(
            status=RepairStatus.ABSTAIN,
            final_sql=sql,
            original_sql=sql,
            confidence=0.0,
            abstain_reason=(
                "Diagnosis identified faults, but no valid AST patches could be constructed within budget. "
                + " | ".join(f"{f.reason} ({f.evidence})" for f in report.faults)[:600]
            ),
            metrics={"latency_ms": round(elapsed_ms, 2)}
        )

    # -------------------------------------------------------------
    # Bước 4: kiểm tra ứng viên qua cổng A tới F
    # -------------------------------------------------------------
    def verify(cand):
        """Chạy Gate A..F cho một ứng viên. Trả về (gate_results, exec_feedback), hoặc None nếu trượt cổng nào đó."""
        cand_sql = cand.repaired_sql
        results = []
        g = verifiers.verify_gate_a_syntax(cand_sql, dialect=dialect)
        if not g.passed:
            return None
        results.append(g)
        for g in (verifiers.verify_gate_b_schema(cand.repaired_ast, schema, dialect=dialect),
                  verifiers.verify_gate_c_type(cand.repaired_ast, schema),
                  verifiers.verify_gate_d_safety(cand_sql, sandbox)):
            if not g.passed:
                return None
            results.append(g)
        g, feed = verifiers.verify_gate_e_execution(cand_sql, sandbox)
        if not g.passed:
            return None
        results.append(g)
        g = verifiers.verify_gate_f_intent(question, cand_sql, evidence)
        if not g.passed:
            return None
        results.append(g)
        return results, feed

    def llm_budget_left():
        return intent is not None and intent.calls < llm_budget

    def accept(cand, gate_results, extra_conf=None, note=None):
        """Đóng gói kết quả REPAIRED cho một ứng viên đã qua mọi cổng."""
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if note:
            gate_results = gate_results[:-1] + [GateResult(
                gate=GateType.GATE_F_INTENT, passed=True,
                confidence=extra_conf if extra_conf is not None else 0.9, message=note)]
        return RepairResult(
            status=RepairStatus.REPAIRED,
            final_sql=cand.repaired_sql,
            original_sql=sql,
            patches_applied=[p.to_record() for p in cand.applied_patches],
            gate_results=gate_results,
            confidence=cand.confidence,
            metrics={
                "latency_ms": round(elapsed_ms, 2),
                "ast_edit_size": cand.ast_edit_size,
                "ast_nodes_changed": SQLParser.calculate_ast_nodes_changed(original_ast, cand.repaired_ast),
                "applied_patch_count": len(cand.applied_patches),
                "llm_calls": intent.calls if intent else 0,
                "intent_log": intent_log,
            }
        )

    def abstain(reason):
        if empty_mode:
            return finish_unchanged()  # câu gốc hợp lệ: không sửa được thì giữ nguyên, không báo từ chối
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return RepairResult(
            status=RepairStatus.ABSTAIN, final_sql=sql, original_sql=sql, confidence=0.0,
            abstain_reason=reason,
            metrics={"latency_ms": round(elapsed_ms, 2),
                     "llm_calls": intent.calls if intent else 0, "intent_log": intent_log})

    rejected_by_intent = 0
    for candidate in candidates:
        verified = verify(candidate)
        if verified is None:
            continue
        gate_results, exec_feed = verified

        if candidate.confidence < active_policy.min_confidence_threshold:
            continue
        # Ứng viên sửa câu "ra rỗng": phải trả về ít nhất 1 dòng mới được tính là đã sửa
        if candidate.must_return_rows and not (exec_feed.row_count or 0) > 0:
            continue

        # Nhiều cách đọc của cùng một lỗi (VD hai khóa ngoại khác nhau): nếu mọi phương án hợp lệ
        # cho cùng kết quả thì chấp nhận; nếu khác nhau thì nhờ LLM chọn (nếu có), không thì từ chối.
        if candidate.agreement_group is not None:
            group = [candidate] + [c for c in candidates
                                   if c is not candidate and c.agreement_group == candidate.agreement_group]
            valid = [(candidate, gate_results, exec_feed)]
            for sib in group[1:]:
                sib_verified = verify(sib)
                if sib_verified is not None:
                    valid.append((sib, sib_verified[0], sib_verified[1]))
            disagree = any(
                v[2].result_set_fingerprint != exec_feed.result_set_fingerprint
                or v[2].row_count != exec_feed.row_count
                for v in valid[1:]
            )
            if disagree:
                if llm_budget_left():
                    idx, why = intent.choose(question, evidence, schema, sql, [v[0].repaired_sql for v in valid])
                    intent_log.append({"type": "choose", "options": len(valid), "picked": idx, "reason": why})
                    if idx is not None:
                        chosen = valid[idx]
                        return accept(chosen[0], chosen[1], extra_conf=0.7,
                                      note=f"LLM chose option {idx + 1}/{len(valid)}: {why}")
                return abstain("Several equally plausible repairs return different results; "
                               "cannot choose without more evidence. Options: "
                               + " || ".join(v[0].repaired_sql for v in valid)[:600])

        # Gate F bằng LLM: loại bản sửa "chạy được nhưng sai ý"
        if intent is not None:
            if not llm_budget_left():
                return abstain("LLM budget exhausted before the repair could be checked against the question.")
            changes = [f"{r.patch_type}: {r.original_fragment} -> {r.replacement_fragment}"
                       for r in (p.to_record() for p in candidate.applied_patches)]
            verdict, why = intent.check(question, evidence, schema, sql, candidate.repaired_sql, changes)
            intent_log.append({"type": "check", "verdict": verdict, "reason": why,
                               "other_issues": intent.last_other_issues,
                               "sql": candidate.repaired_sql[:300]})
            if verdict == "no":
                rejected_by_intent += 1
                continue
            return accept(candidate, gate_results, extra_conf=0.9 if verdict == "yes" else 0.6,
                          note=f"LLM intent check: {verdict} — {why}")

        return accept(candidate, gate_results)

    if rejected_by_intent:
        return abstain(f"{rejected_by_intent} candidate repair(s) ran successfully but the intent check "
                       "judged they do not answer the question.")

    # -------------------------------------------------------------
    # Bước 5: không ứng viên nào qua hết các cổng -> TỪ CHỐI
    # -------------------------------------------------------------
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    if empty_mode:
        return finish_unchanged()
    return RepairResult(
        status=RepairStatus.ABSTAIN,
        final_sql=sql,
        original_sql=sql,
        confidence=0.0,
        abstain_reason="All candidate patches failed verification gates or fell below confidence threshold.",
        metrics={"latency_ms": round(elapsed_ms, 2)}
    )
