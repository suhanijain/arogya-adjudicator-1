"""The claim workflow as a real LangGraph graph. Replaces src/lib/langgraph.ts (a hand-made StateGraph).

supervisor -> extract -> fraud_screen -> coverage -> adjudicator -> critic --(fail, once)--> coverage
                                                                      |
                                              human (interrupt) <-----+---> edit loops back to human
                                                   | approve                | reject -> memory
                                                 settle -> memory
Money is computed only by adjudication_calculator. The model never changes a number.
"""
from __future__ import annotations

import operator
from decimal import Decimal
from typing import Annotated, Any, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..types.adjudication import AdjudicationWorksheet, ExtractedClaim, FraudReport
from . import llm
from .adjudication_calculator import calculate_worksheet, recompute_totals
from .claims_db import claims_db, now
from .claims_metrics import CLAIMS, DEDUCTED, DECISIONS, CRITIC_REVISIONS, FRAUD_FLAGS, SETTLED, timed
from .fraud_detector import detect_fraud
from .vector_db import hybrid_clauses
from .privacy import mask, set_phi
from .reflexion_engine import reflexion_engine

D = Decimal
COMPLEX_LIMIT = D(100000)


class State(TypedDict, total=False):
    claim: ExtractedClaim
    fraud: FraudReport
    ws: AdjudicationWorksheet
    auto_approve: Optional[dict]          # {"approver": ..., "level": "junior"|"senior"} for batch runs and tests
    lessons: list[str]
    outcome: str                          # settled | repudiated | rejected | pending
    settlement: dict
    steps: Annotated[list[dict], operator.add]   # audit trail; masked before it is stored


def step(node: str, summary: str, **extra) -> dict:
    return {"steps": [{"node": node, "at": now(), "summary": mask(summary), **extra}]}


# ---- nodes -------------------------------------------------------------------------------
def supervisor(s: State):
    c = s["claim"]
    set_phi(c)
    return step("supervisor", f"Routing claim {c.claim_id}: {len(c.line_items)} bill lines, claimed ₹{c.claimed_total}.")


def extract(s: State):
    c = s["claim"]
    got = sum((i.claimed_amount for i in c.line_items), D(0))
    ok = got == c.claimed_total
    return step("extract", f"Line items sum to ₹{got}; claim form says ₹{c.claimed_total}. {'Match.' if ok else 'MISMATCH, flagged for the adjudicator.'}")


def fraud_screen(s: State):
    with timed("fraud_screen"):
        r = detect_fraud(s["claim"])
    for f in r.flags:
        FRAUD_FLAGS.labels(f.type).inc()
    return {"fraud": r, **step("fraud_screen", f"Risk {r.risk_score}/100, {len(r.flags)} flag(s): {', '.join(f.type for f in r.flags) or 'none'}.")}


def coverage(s: State):
    c, r = s["claim"], s["fraud"]
    with timed("coverage"):
        ws = calculate_worksheet(c, cataract_previously_used=claims_db.used_sublimits(c.policy_number).get("cataract", D(0)))
    prev = s.get("ws")
    if prev:
        ws = ws.model_copy(update={"revisions_count": prev.revisions_count + 1})
    if r.has_fraud_flag:
        ws.requires_senior_approval = True
        ws.senior_approval_reasons = [*ws.senior_approval_reasons, f"Fraud screen: {len(r.flags)} flag(s), risk {r.risk_score}."]
    return {"ws": ws, **step("coverage", f"Deductions ₹{ws.total_deductions}, co-pay ₹{ws.copay_amount}, payable ₹{ws.final_payable_amount}.")}


def adjudicator(s: State):
    """Complex cases get a BM25-grounded model note. Simple ones skip the model entirely."""
    c, ws = s["claim"], s["ws"]
    triggers = []
    if ws.final_payable_amount >= COMPLEX_LIMIT: triggers.append("High-value claim")
    if ws.room_rent_breach: triggers.append("Room rent breach with proportional cut")
    if c.is_pre_existing: triggers.append("Pre-existing condition declared")
    if s["fraud"].has_fraud_flag: triggers.append("Fraud or attack flag")
    if not ws.line_sum_matches_bill: triggers.append("Bill total does not match line items")
    lessons = reflexion_engine.retrieve(c, ws)
    ws.is_complex_case, ws.complex_case_triggers = bool(triggers), triggers
    if not triggers:
        llm.LLM_CALLS.labels("skipped").inc()
        return {"ws": ws, "lessons": [l.id for l in lessons], **step("adjudicator", "Straightforward case: rules only, no model call.", llm=False)}
    docs = hybrid_clauses(" ".join([c.diagnosis, c.procedure_name, *triggers]), k=4)      # BM25 + vector database
    clauses = "\n".join(f"{d.metadata['clause']} {d.metadata['title']}: {d.page_content}" for d in docs)
    # Only structured fields go to the model. Raw document text (where an attack could hide) does not.
    data = {"claimId": c.claim_id, "age": c.patient_age, "diagnosis": c.diagnosis, "procedure": c.procedure_name,
            "claimed": str(c.claimed_total), "payable": str(ws.final_payable_amount), "deductions": {k: str(v) for k, v in ws.deductions_by_reason.items() if v},
            "flags": [f.title for f in s["fraud"].flags]}
    fallback = "Rules-only analysis. Triggers: " + "; ".join(triggers) + ". Clauses retrieved: " + ", ".join(d.metadata["clause"] for d in docs) + "."
    out = llm.analyze_case(triggers=triggers, clauses=clauses, lessons="\n".join(f"{l.id}: {l.lesson_rule}" for l in lessons), claim_data=data, fallback=fallback)
    ws.llm_adjudication = out
    return {"ws": ws, "lessons": [l.id for l in lessons], **step("adjudicator", f"Complex case ({'; '.join(triggers)}). Model: {out['model']}.", llm=out["is_ai"])}


def critic(s: State):
    """Independent checks on the worksheet. One send-back, then a human decides."""
    ws, c = s["ws"], s["claim"]
    problems = []
    if any(l.deduction_amount > 0 and not l.clause_cited for l in ws.lines):
        problems.append("A deduction has no clause cited.")
    if ws.room_rent_breach and any(l.category == "medicines" and l.deduction_amount > 0 and l.deduction_reason and "proportional" in l.deduction_reason.lower() for l in ws.lines):
        problems.append("Medicines were cut proportionally (exempt under Clause 4.1 Note b).")
    if ws.total_deductions + ws.admissible_before_copay != c.claimed_total:
        problems.append("Deductions plus admissible amount do not equal the claimed total.")
    if ws.admissible_before_copay - ws.copay_amount != ws.final_payable_amount and ws.final_payable_amount != c.total_coverage_limit:
        problems.append("Payable amount does not equal admissible minus co-pay.")
    ws.critic_status = "revision_requested" if problems else "passed"
    ws.critic_discrepancies, ws.critic_notes = problems, "; ".join(problems) or "All checks passed."
    if problems and ws.revisions_count < 1:
        CRITIC_REVISIONS.inc()
    return {"ws": ws, **step("critic", ws.critic_notes)}


def after_critic(s: State) -> str:
    ws = s["ws"]
    return "coverage" if ws.critic_status == "revision_requested" and ws.revisions_count < 1 else "human"


def human(s: State):
    """Pause for an adjudicator. interrupt() stores the state; Command(resume=...) continues it."""
    ws, c = s["ws"], s["claim"]
    if not ws.is_admissible:
        return {"outcome": "repudiated", **step("human", f"Repudiation reviewed automatically: {ws.inadmissibility_reason}")}
    decision = s.get("auto_approve") or interrupt({"claim_id": c.claim_id, "worksheet_hash": ws.worksheet_hash, "payable": str(ws.final_payable_amount),
                                                   "requires_senior": ws.requires_senior_approval, "reasons": ws.senior_approval_reasons})
    action, who, level = decision.get("action", "approve"), decision["approver"], decision.get("level", "junior")
    DECISIONS.labels(action, level).inc()
    if action == "reject":
        claims_db.log_decision(c.claim_id, "reject", who, level)
        ws.approval_state, ws.rejection_reason = "rejected", decision.get("reason", "")
        return {"ws": ws, "outcome": "rejected", **step("human", f"Rejected by {who}: {ws.rejection_reason}")}
    if action == "edit":
        lines, n = [l.model_copy() for l in ws.lines], 0
        for e in decision["edits"]:
            l = next(x for x in lines if x.line_id == e["line_id"])
            original = l.deduction_amount
            l.deduction_amount, l.admissible_amount = D(str(e["deduction_amount"])), l.claimed_amount - D(str(e["deduction_amount"]))
            l.edited_by_adjudicator, l.adjudicator_comment = True, e.get("comment", "")
            reflexion_engine.propose_from_edit(c, l, original, l.adjudicator_comment)
            n += 1
        reflexion_engine.record_outcome(s.get("lessons", []), edited=True)
        claims_db.log_decision(c.claim_id, "edit", who, level, n)
        # Auto-approval is a one-shot for batch runs; after an edit a person must approve the new version.
        return {"ws": recompute_totals(c, ws, lines), "auto_approve": None, **step("human", f"{who} edited {n} line(s); earlier approval voided, new hash issued.")}
    if ws.requires_senior_approval and level != "senior":
        return {**step("human", f"{who} ({level}) cannot approve: senior approval is required ({'; '.join(ws.senior_approval_reasons)}).")}
    if ws.requires_senior_approval and ws.junior_adjudicator_name and who == ws.junior_adjudicator_name:
        return {**step("human", f"Four-eyes rule: {who} already reviewed this claim and cannot also give the senior approval.")}
    ws.approval_state = "senior_approved" if level == "senior" else "junior_approved"
    ws.junior_adjudicator_name = ws.junior_adjudicator_name or who
    if level == "senior": ws.senior_adjudicator_name = who
    ws.approval_token = claims_db.issue_token(c.claim_id, ws.worksheet_hash, who, level)
    claims_db.log_decision(c.claim_id, "approve", who, level)
    reflexion_engine.record_outcome(s.get("lessons", []), edited=False)
    return {"ws": ws, "outcome": "approved", **step("human", f"Approved by {who} ({level}); token bound to worksheet {ws.worksheet_hash[:12]}.")}


def after_human(s: State) -> str:
    ws, out = s["ws"], s.get("outcome", "")
    if out == "approved": return "settle"
    if out in ("rejected", "repudiated"): return "memory"
    return "human"          # edited, or approval refused: ask again


def settle(s: State):
    ws, c = s["ws"], s["claim"]
    sub = {r.sublimit_key: float(r.admissible) for r in ws.sublimits_applied}
    res = claims_db.settle_claim(caller="Settlement", claim_id=c.claim_id, policy_no=c.policy_number, claimant=c.insured_name, expected_name=ws.insured_name,
                                 token=ws.approval_token, ws_hash=ws.worksheet_hash, amount=ws.final_payable_amount, bill_no=c.bill_number, sublimits_used=sub)
    if not res["success"]:
        return {"settlement": res, "outcome": "blocked", **step("settle", res["error"])}
    ws.settlement_issued = True
    SETTLED.inc(float(ws.final_payable_amount))
    return {"ws": ws, "settlement": res, "outcome": "settled", **step("settle", f"Settled ₹{ws.final_payable_amount} ({res['settlementId']}).")}


def memory(s: State):
    ws = s["ws"]
    out = s.get("outcome", "pending")
    CLAIMS.labels(out).inc()
    for k, v in ws.deductions_by_reason.items():
        if v: DEDUCTED.labels(k).inc(float(v))
    return step("memory", f"Outcome {out} recorded for audit and the playbook.")


def build_graph(checkpointer=None):
    g = StateGraph(State)
    for name, fn in [("supervisor", supervisor), ("extract", extract), ("fraud_screen", fraud_screen), ("coverage", coverage),
                     ("adjudicator", adjudicator), ("critic", critic), ("human", human), ("settle", settle), ("memory", memory)]:
        g.add_node(name, fn)
    g.add_edge(START, "supervisor")
    for a, b in [("supervisor", "extract"), ("extract", "fraud_screen"), ("fraud_screen", "coverage"), ("coverage", "adjudicator"), ("adjudicator", "critic"), ("settle", "memory"), ("memory", END)]:
        g.add_edge(a, b)
    g.add_conditional_edges("critic", after_critic, {"coverage": "coverage", "human": "human"})
    g.add_conditional_edges("human", after_human, {"settle": "settle", "memory": "memory", "human": "human"})
    # Allow our own pydantic models in checkpoints (silences the unregistered-type warning, stays strict for others).
    serde = JsonPlusSerializer(allowed_msgpack_modules=[("arogya.types.adjudication", n) for n in ("ExtractedClaim", "FraudReport", "AdjudicationWorksheet")])
    return g.compile(checkpointer=checkpointer or MemorySaver(serde=serde))
