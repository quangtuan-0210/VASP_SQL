"""
VASP-SQL Core Repair Pipeline: Orchestrates Diagnosis, Patching, and Verification.
"""

import time
from typing import Optional

from vasp_sql.ast.parser import SQLParser
from vasp_sql.ast.scope import ScopeAnalyzer
from vasp_sql.core.feedback import ExecutionFeedback
from vasp_sql.core.policy import RepairPolicy
from vasp_sql.core.result import GateResult, GateType, RepairResult, RepairStatus
from vasp_sql.core.schema import SchemaMetadata
from vasp_sql.diagnosis.engine import DiagnosisEngine
from vasp_sql.repair.planner import RepairPlanner
from vasp_sql.sandbox.executor import SQLiteSandboxExecutor
from vasp_sql.schema_graph.graph import SchemaKnowledgeGraph
from vasp_sql.verifiers.gates import VerificationPipeline
from vasp_sql.verifiers.intent import IntentVerifier


def repair(
    question: str,
    sql: str,
    schema: SchemaMetadata,
    db_path: str,
    dialect: str = "sqlite",
    evidence: Optional[str] = None,
    execution_feedback: Optional[ExecutionFeedback] = None,
    policy: Optional[RepairPolicy] = None,
    llm=None
) -> RepairResult:
    """
    Main entry point for VASP-SQL repair layer.
    Follows Algorithm 1 from VASP-SQL design:
    1. Validate original query. If all gates pass -> return UNCHANGED.
    2. If original query fails -> diagnose localized faults.
    3. Plan localized AST candidate patches within RepairBudget.
    4. Verify candidates sequentially through Gates A to F.
    5. Return highest-ranked candidate passing all gates -> REPAIRED.
    6. If no candidate passes -> safely ABSTAIN.

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

    # -------------------------------------------------------------
    # Step 1: Check if original query is already fully valid
    # -------------------------------------------------------------
    gate_a = verifiers.verify_gate_a_syntax(sql, dialect=dialect)
    original_ast = None

    if gate_a.passed:
        original_ast = parser.parse(sql, dialect=dialect)
        gate_b = verifiers.verify_gate_b_schema(original_ast, schema, dialect=dialect)
        gate_c = verifiers.verify_gate_c_type(original_ast, schema)
        gate_d = verifiers.verify_gate_d_safety(sql, sandbox)

        if gate_b.passed and gate_c.passed and gate_d.passed:
            gate_e, exec_feed = verifiers.verify_gate_e_execution(sql, sandbox)
            gate_f = verifiers.verify_gate_f_intent(question, sql, evidence)

            if gate_e.passed and gate_f.passed:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return RepairResult(
                    status=RepairStatus.UNCHANGED,
                    final_sql=sql,
                    original_sql=sql,
                    patches_applied=[],
                    gate_results=[gate_a, gate_b, gate_c, gate_d, gate_e, gate_f],
                    confidence=1.0,
                    metrics={"latency_ms": round(elapsed_ms, 2), "ast_edit_size": 0}
                )

    # -------------------------------------------------------------
    # Step 2: Query has errors -> Run Multi-layer Diagnosis
    # -------------------------------------------------------------
    report = diagnosis_engine.diagnose(
        sql=sql,
        schema=schema,
        kg=kg,
        execution_feedback=execution_feedback,
        dialect=dialect
    )

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
    # Step 3: Plan candidate AST patches subject to budget
    # -------------------------------------------------------------
    candidates = planner.plan_candidates(
        original_ast=original_ast,
        report=report,
        schema=schema,
        kg=kg,
        budget=active_policy.budget,
        dialect=dialect
    )

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
    # Step 4: Verify candidates through Gates A to F
    # -------------------------------------------------------------
    def verify(cand):
        """Run Gates A..F on one candidate. Returns (gate_results, exec_feedback) or None if any gate fails."""
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
                "applied_patch_count": len(cand.applied_patches),
                "llm_calls": intent.calls if intent else 0,
                "intent_log": intent_log,
            }
        )

    def abstain(reason):
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
            verdict, why = intent.check(question, evidence, schema, sql, candidate.repaired_sql)
            intent_log.append({"type": "check", "verdict": verdict, "reason": why,
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
    # Step 5: None of the candidates passed all gates -> ABSTAIN
    # -------------------------------------------------------------
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    return RepairResult(
        status=RepairStatus.ABSTAIN,
        final_sql=sql,
        original_sql=sql,
        confidence=0.0,
        abstain_reason="All candidate patches failed verification gates or fell below confidence threshold.",
        metrics={"latency_ms": round(elapsed_ms, 2)}
    )
