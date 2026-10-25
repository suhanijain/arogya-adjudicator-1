"""Adjudicator console data: worklist, claim bench, rule matrix, audit trail, KPIs. Everything is computed, nothing is canned."""
import hashlib
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from api_360 import adjudicator
from arogya.data.synthetic_claims import get_claim, load_claims
from arogya.lib import agent_orchestrator as orch
from arogya.lib.adjudication_calculator import calculate_worksheet
from arogya.lib.claim_preapproval import claim_preapprovals as cpa
from arogya.lib.claims_db import claims_db
from arogya.lib.fraud_detector import detect_fraud
from arogya.lib import vector_db
from arogya.lib.policy_kb import CLAUSES, get_clause, normalize_bill_term
from arogya.lib.preauth import preauth
from arogya.lib.reflexion_engine import reflexion_engine

public = APIRouter(prefix="/api/policy")
adj = APIRouter(prefix="/api/adjudicator", dependencies=[Depends(adjudicator)])
dump = lambda m: m.model_dump(mode="json", by_alias=True)


@public.get("/clauses")
def clauses():
    return [{"number": c.clause_number, "title": c.title, "section": c.section, "text": c.text, "ruleType": getattr(c, "rule_type", ""), "tags": getattr(c, "tags", [])} for c in CLAUSES]


@public.get("/search")
def search(q: str, k: int = 5, mode: str = "hybrid"):
    """mode: bm25 (keywords), vector (meaning, from the vector database) or hybrid (both, fused)."""
    fn = {"bm25": vector_db.bm25_clauses, "vector": lambda q, k: vector_db.semantic_clauses(q, k), "hybrid": vector_db.hybrid_clauses}.get(mode)
    if not fn:
        raise HTTPException(400, "mode must be bm25, vector or hybrid")
    return [{**d.metadata, "text": d.page_content} for d in fn(q, min(k, 10))]


@public.get("/vector-db")
def vector_db_info():
    return vector_db.stats()


@public.get("/eval")
def retrieval_eval():
    """BM25 vs vector vs hybrid, measured on the paraphrase question sets (see eval_retrieval.py)."""
    import eval_retrieval
    return {"embedder": vector_db.embedder_name(), **{n: {"queries": len(eval_retrieval.SETS[n]), "modes": {m: {k: r[k] for k in ("recall@3", "mrr")} for m, r in eval_retrieval.evaluate(n).items()}} for n in eval_retrieval.SETS}}


@public.get("/normalize")
def normalize(term: str):
    """A hospital's own wording -> standard bill head: exact or substring match first, then nearest in the vector database."""
    m = normalize_bill_term(term)
    if m.get("normalized") == term:
        s = vector_db.semantic_term(term)
        if s:
            return {**s, "via": "vector"}
        return {**m, "via": "none"}
    return {**m, "via": "text"}


def ws_for(c):
    return calculate_worksheet(c, cataract_previously_used=claims_db.used_sublimits(c.policy_number).get("cataract", Decimal(0)))


@adj.get("/worklist")
def worklist():
    rows = []
    for c in load_claims():
        ws, fr, snap = ws_for(c), detect_fraud(c), orch.snapshot(c.claim_id)
        rows.append({"claimId": c.claim_id, "patient": c.insured_name, "age": c.patient_age, "gender": c.patient_gender, "hospital": c.hospital_name, "tier": c.city_tier,
                     "diagnosis": c.diagnosis, "icd": c.icd10_code, "procedure": c.procedure_name, "claimed": float(c.claimed_total), "payable": float(ws.final_payable_amount),
                     "deducted": float(ws.total_deductions), "admissible": ws.is_admissible, "risk": fr.risk_score, "flagged": fr.has_fraud_flag,
                     "flags": [f.type for f in fr.flags], "senior": ws.requires_senior_approval or fr.has_fraud_flag, "roomBreach": ws.room_rent_breach,
                     "run": snap["status"], "outcome": snap.get("outcome")})
    return rows


@adj.get("/claims/{cid}")
def bench(cid: str):
    c = get_claim(cid)
    if not c:
        raise HTTPException(404, "unknown claim")
    ws, fr = ws_for(c), detect_fraud(c)
    seen = {}
    for l in ws.lines:
        for part in (l.clause_cited or "").split(" & "):
            cl = get_clause(part.strip()) if part.strip() else None
            if cl:
                seen[cl.clause_number] = {"number": cl.clause_number, "title": cl.title, "text": cl.text}
    return {"claim": {k: v for k, v in dump(c).items() if k not in ("rawDocuments", "lineItems")}, "worksheet": dump(ws), "fraud": dump(fr), "clauses": list(seen.values()),
            "trace": orch.snapshot(cid), "documents": [{"name": n, "chars": len(t)} for n, t in (("Claim form", c.raw_documents.claim_form_text), ("Discharge summary", c.raw_documents.discharge_summary_text),
                                                                                              ("Itemised bill", c.raw_documents.itemised_bill_text), ("Policy schedule", c.raw_documents.policy_schedule_text)) if t]}


@adj.get("/claims/{cid}/similar")
def similar(cid: str, k: int = 5):
    c = get_claim(cid)
    if not c:
        raise HTTPException(404, "unknown claim")
    rows = vector_db.similar_claims(f"{c.diagnosis}. {c.procedure_name}. {c.icd10_code}. {c.hospital_name}", k=k, exclude=cid)
    out = []
    for r in rows:
        o = get_claim(r["claim"])
        w = ws_for(o)
        out.append({**r, "claimed": float(o.claimed_total), "payable": float(w.final_payable_amount), "admissible": w.is_admissible, "diagnosis": o.diagnosis, "procedure": o.procedure_name})
    return out


@adj.get("/overview")
def overview():
    cs = load_claims()
    wss = [ws_for(c) for c in cs]
    frs = [detect_fraud(c) for c in cs]
    pr = list(preauth.requests.values())
    ft = list(cpa.items.values())
    settled_ft = [x for x in ft if x["status"] == "settled"]
    return {"claims": len(cs), "flagged": sum(f.has_fraud_flag for f in frs), "repudiated": sum(not w.is_admissible for w in wss),
            "claimed": float(sum(c.claimed_total for c in cs)), "deducted": float(sum(w.total_deductions for w in wss)), "payable": float(sum(w.final_payable_amount for w in wss)),
            "sessionRequests": {"total": len(pr), "cleared": sum(x["status"] == "cleared" for x in pr), "held": sum(x["status"] == "held" for x in pr),
                                "awaiting": sum(x["status"] == "awaiting" for x in pr), "approved": sum(x["status"] == "approved" for x in pr)},
            "fastTrack": {"total": len(ft), "preapproved": sum(x["status"] == "preapproved" for x in ft), "settled": len(settled_ft), "needDecision": sum(x["status"] == "adjudication" for x in ft),
                          "standardReview": sum(x["status"] == "standard_review" for x in ft),
                          "autoSettled": sum(1 for x in settled_ft if x["reconciliation"]["level"] == "system"),
                          "netAdjustment": sum(x["reconciliation"]["delta"] for x in settled_ft)},
            "standingActive": sum(not a["revoked"] for a in preauth.standing.values()), "decisions": len(claims_db.decisions),
            "lessons": reflexion_engine.stats()}


@adj.get("/audit")
def audit():
    rows = [{"at": d["at"], "ref": d["claim_id"], "action": d["action"], "approver": d["approver"], "level": d["level"]} for d in claims_db.decisions]
    rows += [{"at": s["at"], "ref": cid, "action": "settled", "approver": s.get("by", "Settlement agent"), "level": "", "settlementId": s["settlement_id"]} for cid, s in claims_db.settled.items()]
    for r in rows:
        r["hash"] = hashlib.sha256(f"{r['at']}|{r['ref']}|{r['action']}|{r['approver']}".encode()).hexdigest()[:16]
    return sorted(rows, key=lambda r: r["at"], reverse=True)
