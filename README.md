# Arogya Sanjeevani claims adjudication (Python)

A simple Python port of the AI Studio workstation (React + Express). It keeps the original's module layout, so you can read the two side by side.

| TypeScript | Python | Notes |
|---|---|---|
| `src/types/adjudication.ts` | `arogya/types/adjudication.py` | pydantic v2, same camelCase JSON |
| `lib/adjudicationCalculator.ts` | `lib/adjudication_calculator.py` | Decimal, whole-rupee rounding like `Math.round` |
| `lib/langgraph.ts` (hand-made) | `lib/claims_graph.py` | real **LangGraph**, `interrupt()` for the adjudicator |
| `lib/vectorDb.ts` (fake embeddings) | `lib/policy_kb.py` | **BM25** (`rank_bm25`) behind a **LangChain** `BaseRetriever` |
| `/api/gemini/*` in `server.ts` | `lib/llm.py` | LangChain chains, **LangSmith** `@traceable` with masking |
| `lib/documentExtractor` | `lib/document_extractor.py` | rules first, **DSPy** `Predict` only for lines the rules can't place |
| `lib/claimsMetrics.ts` | `lib/claims_metrics.py` | **Prometheus** metrics, **Grafana** dashboard in `ops/` |
| `lib/fraudDetector.ts`, `reflexionEngine.ts`, `claimsDb.ts`, `tariffDb.ts` | same names in snake_case | |
| `server.ts` | `server.py` (FastAPI) | |

## Run
```
pip install -r requirements.txt
make test            # 58 tests
make serve           # app at http://localhost:8000 , API docs at /docs
make eval            # DSPy Evaluate on the extractor (80/80)
docker compose up    # api + Prometheus (9090) + Grafana (3000, dashboard "Arogya claims")
```
Models are open source, served by [Ollama](https://ollama.com): `ollama pull llama3.1:8b` (or set `OLLAMA_MODEL`). LangChain uses `ChatOllama`; DSPy uses the same model through `llm.configure_dspy()`. If Ollama isn't running, the model steps use deterministic text and everything else works. Set `LANGSMITH_TRACING=true` and a key to trace; health details are masked before they leave.

## Three views (open `/`)
- **Patient**: cover left, ongoing treatment (found from history), standing pre-approval and sessions left, request status in plain words, recent treatments and payments.
- **Network hospital**: request approval for a session (answered in seconds when it fits the patient's pattern), see its own requests and a billing scorecard.
- **Adjudicator** (name, level and key in the top bar): **Approval queue**, **Patient 360**, **Network 360**.
  - *Patient 360*: payments to date, regular treatments, a timeline by hospital with out-of-pattern sessions ringed, standing approvals (revocable), and why each past or open session differs.
  - *Network 360*: each hospital's billing vs benchmark, claim flag rate, repudiation rate, share of patients' sessions that break their own pattern, monthly volume.

How it speeds up regular treatment (dialysis, chemotherapy, physiotherapy): the first request for a patient with a steady pattern gets a suggested **standing authorisation** (hospitals, per-session cap, number of sessions, end date). One adjudicator approval covers the coming sessions; each one is still checked, and clears in seconds if it matches. A session that differs is **held** for a person and never rejected automatically. Checks (`lib/patient_360.py`): new contact (someone else may be using the account), new hospital, other city, amount above 1.25x the patient's usual, too soon after the last session, price above tariff ceiling, cover left. Approving a held request needs a senior and a note on how the patient was verified on the registered number. Patients and hospitals see only a status, never the reasons.

Data: `scripts/make_history.py` builds 12 synthetic patients (368 past sessions) and 12 pending requests. Each pattern break is planted on purpose (PA-1002 other city, PA-1003 amount, PA-1004 new phone, PA-1005 same-day repeat) and Sterling Hospital bills dialysis at about 2x benchmark. "Today" is fixed at 2026-10-26 so results are reproducible.

## Claim pre-approval for regular treatment (fast-track)
Hospital: **Pre-approve a regular treatment** → assessed from the hospital's contracted package and room rates in the linked provider database (`data/provider_rates.json`, built by `scripts/make_provider_rates.py`; a live system would sync each provider's rate file) and **guaranteed at once** after the 5% co-payment. After treatment: **Send bill**.
Eligibility (all must pass; each check and its reason is shown to adjudicators, never to patients or hospitals): a regular treatment pattern; the patient's usual network hospital with no pricing or claim-flag review; no past out-of-pattern session; no medium or high fraud flag on the patient's claim record; no open hold or rejected request; session timing, phone and price match the pattern; contracted room rate within the policy cap; cover left at least 1.2x the assessed amount and policy valid. Anyone else gets standard review.
Reconciliation uses only the itemised bill and the discharge summary, and only moves the amount:
- room days limited to those the discharge summary documents (extra documented days move it **up**, padded days **down**);
- room rate limited to the hospital's contracted rate (down);
- non-payable items removed (down, Clause 4.7); a bill below the package moves it down; up to 10% above the package is allowed;
- 5% co-payment, and never more than cover left.
It settles automatically only when nothing else is wrong. These go to an adjudicator with the proposed amount (a senior must approve, with a note): line arithmetic or total errors, duplicate bill number, admission far from the planned date, discharge summary dates not matching its room days, room rate above the policy cap (a proportional cut would be needed), bill more than 10% above the package, or payable more than 25% above the pre-approved amount. Nothing is rejected automatically. A settled session becomes history for the next check, so a repeat on the same day is held.
Automatic approval within these limits is a policy decision (`fast-track-policy-v1` is recorded as the approver): your compliance owner should sign off the limits before real use.

## Vector database and semantic search (`lib/vector_db.py`)
Policy clauses (`data/policy_clauses`) plus a plain-language glossary (`data/glossary.json`: how people phrase things, e.g. "stretcher van" for ambulance) are embedded and stored in a persistent **Chroma** collection (`.vectordb/`, cosine distance, via `langchain-chroma`). The collection name carries a fingerprint of the content and embedder, so changed clauses or a new embedder rebuild automatically (about 8 s at first start; a warm-up thread runs at server start).
- **Embeddings**: Ollama `nomic-embed-text` (`ollama pull nomic-embed-text`) when the server is running; otherwise an offline fallback, LSA (TF-IDF over words and character n-grams, reduced with TruncatedSVD, L2-normalised). The UI sidebar shows which one is active.
- **Retrieval** (`hybrid_clauses`, used by the claims graph): BM25 and vector search are merged by weighted reciprocal rank fusion (BM25 weight 1, vector weight 2). A LangChain `BaseRetriever` wraps it.
- **Evaluate**: `python eval_retrieval.py` (a development set used to pick the weights, and a held-out set written afterwards).

Offline LSA results here (recall@3 / MRR):
| set | BM25 | vector | hybrid |
|---|---|---|---|
| development (24 queries) | 0.75 / 0.72 | 1.00 / 0.93 | 0.88 / 0.83 |
| held-out (12 queries) | 0.75 / 0.69 | 0.83 / 0.69 | 0.83 / 0.69 |

Read these with care: the glossary and the queries were written by the same author, so the numbers are optimistic; 12 queries is a small sample; and hybrid did **not** beat vector alone on either set, so the weights are a judgement, not a proven gain. The neural embedder was not tested here (no Ollama or model downloads in the sandbox).

## Console (Stitch-style UI)
`static/` is a dependency-free SPA in the dark "Clinical Adjudication Engine" style from the supplied Stitch export (tokens, inline icons). Adjudicator tabs: Claims worklist, Adjudication bench (ledger, clauses, pipeline trace, retrieval), Session queue, Patient 360, Network 360, Fast-track, Rules, Audit; plus Patient and Network hospital views; layout works down to 390 px. Fonts load from Google Fonts when online and fall back to system fonts. The bench has no line-edit screen yet (the edit API exists). Checked in Chromium only.

## Flow
supervisor → extract → fraud_screen → coverage → adjudicator → critic (one send-back) → human (pauses) → settle → memory.
`POST /api/claims/{id}/run`, then `POST /api/claims/{id}/decision` with `{action: approve|edit|reject, approver, level}`.

## Deliberate differences from the original
- Waiting-period rules are checked independently (the original skipped one: CLM-004, CLM-044 differ).
- Excluded goods are a low-severity note, not fraud. Tariff outliers are one consolidated rule (27 flags vs the original's 17).
- Hashes are real SHA-256; approval tokens are HMAC-signed and bound to the worksheet hash. An edit voids approval. Senior cases need a senior who is not the junior reviewer.
- Reflexion lessons carry a machine-checked `applies_when` and live fired/edited counters; the original's hard-coded "88.2%" figures are gone.
- Only structured fields and BM25 clauses go to the model, never raw document text.
- Outside those, the calculator matches the TS output exactly (differential test on 80 claims).

## Limits of the new views
Demo access control only: patients are chosen by policy number, hospitals by name, adjudicators by an API key (`ADJUDICATOR_KEY`, default `demo-key`; level is self-declared). Real use needs OTP sign-in for patients and SSO for staff. Thresholds (1.25x, 80% steady rhythm, 4 sessions) are reasonable defaults, not tuned on real data. Dialysis, chemotherapy and physiotherapy tariffs are illustrative, and whether each is payable under Arogya Sanjeevani is assumed, not checked against the policy wording. Hospital claim counts are about 8 each, so claim flag rates are noisy. Requests live in memory and reset on restart. The browser UI was checked in Chromium only.

## Not ported / not verified
React UI, PDF generator, Java bridge, the TS data generator (replaced by `data/synthetic_claims.json`). The live model paths (Ollama via LangChain and DSPy, LangSmith upload) and the Docker build were not run here; there was no Ollama server, model weights or Docker here. Category rules are tuned to the synthetic vocabulary, so expect DSPy fallbacks on real bills.
