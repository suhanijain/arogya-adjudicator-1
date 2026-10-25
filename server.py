"""FastAPI server. Mirrors server.ts (+ the claim, decision and lesson routes the React app called in-process)."""
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from api_console import adj as console_adj, public as policy_api
from api_360 import adj_api, adjudicator, hospital_api, patient_api
from arogya.data.synthetic_claims import get_claim, load_claims
from arogya.lib import agent_orchestrator as orch, llm
from arogya.lib.adjudication_calculator import calculate_worksheet
from arogya.lib.claims_metrics import LESSONS, summarize
from arogya.lib.document_extractor import extract_claim
from arogya.lib.fraud_detector import detect_fraud
from arogya.lib.reflexion_engine import reflexion_engine

import threading

from arogya.lib import vector_db

app = FastAPI(title="Arogya Sanjeevani claims adjudication")
llm.configure_dspy()   # no-op unless Ollama is running
threading.Thread(target=vector_db.stats, daemon=True).start()      # build or open the vector database in the background


class Decision(BaseModel):
    action: str = "approve"
    approver: str
    level: str = "junior"
    edits: list[dict] = []
    reason: str = ""


class Review(BaseModel):
    reviewer: str
    note: str = ""


def claim_or_404(cid: str):
    c = get_claim(cid)
    if c is None:
        raise HTTPException(404, "unknown claim")
    return c


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/api/claims")
def claims():
    return [{"claimId": c.claim_id, "diagnosis": c.diagnosis, "claimedTotal": c.claimed_total} for c in load_claims()]


@app.get("/api/claims/{cid}")
def claim(cid: str):
    return claim_or_404(cid).model_dump(mode="json", by_alias=True, exclude={"raw_documents"})


@app.post("/api/claims/{cid}/run")
def run(cid: str):
    claim_or_404(cid)
    return orch.run_claim(cid)


@app.get("/api/claims/{cid}/state")
def state(cid: str):
    return orch.snapshot(cid)


@app.post("/api/claims/{cid}/decision", dependencies=[Depends(adjudicator)])
def decision(cid: str, d: Decision):
    try:
        return orch.decide(cid, d.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/llm/settlement-report")
def settlement_report(body: dict):
    try:
        return orch.settlement_letter(body["claimId"])
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/extract-document")
def extract_document(docs: dict):
    claim, warnings = extract_claim(docs)
    return {"claim": claim.model_dump(mode="json", by_alias=True, exclude={"raw_documents"}), "warnings": warnings}


@app.get("/api/lessons")
def lessons():
    return reflexion_engine.stats()


@app.post("/api/lessons/{lid}/{action}", dependencies=[Depends(adjudicator)])
def review_lesson(lid: str, action: str, r: Review):
    try:
        return reflexion_engine.review(lid, action, r.reviewer, r.note)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/metrics/summary")
def metrics_summary():
    cs = load_claims()
    wss = [calculate_worksheet(c) for c in cs]
    return summarize(cs, wss, [detect_fraud(c) for c in cs])


@app.get("/metrics")
def metrics():
    for status in ("pending_review", "approved", "rejected", "withdrawn"):
        LESSONS.labels(status).set(sum(l.status == status for l in reflexion_engine.all()))
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


for r in (patient_api, hospital_api, adj_api, console_adj, policy_api):
    app.include_router(r)
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
