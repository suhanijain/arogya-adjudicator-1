"""Metrics. Mirrors src/lib/claimsMetrics.ts: Prometheus counters for Grafana, and a dataset summary."""
from collections import Counter as Count
from contextlib import contextmanager
from decimal import Decimal
import time

from prometheus_client import Counter, Gauge, Histogram

CLAIMS = Counter("arogya_claims_total", "Claims that finished the workflow", ["outcome"])
NODE_SECONDS = Histogram("arogya_node_seconds", "Time spent in each graph node", ["node"], buckets=(0.005, 0.02, 0.1, 0.5, 2, 10))
DEDUCTED = Counter("arogya_deducted_rupees_total", "Rupees deducted, by reason", ["reason"])
SETTLED = Counter("arogya_settled_rupees_total", "Rupees settled")
FRAUD_FLAGS = Counter("arogya_fraud_flags_total", "Fraud and attack flags raised", ["type"])
CRITIC_REVISIONS = Counter("arogya_critic_revisions_total", "Times the critic sent a worksheet back")
DECISIONS = Counter("arogya_decisions_total", "Adjudicator decisions", ["action", "level"])
PENDING = Gauge("arogya_approvals_pending", "Claims waiting for an adjudicator")
LLM_CALLS = Counter("arogya_llm_calls_total", "Model calls", ["mode"])          # live | fallback | skipped
PREAUTH = Counter("arogya_preauth_total", "Pre-authorisation requests by result", ["result"])
PREAPPROVAL = Counter("arogya_claim_preapproval_total", "Claim pre-approvals by result", ["result"])
ADJUSTMENT = Counter("arogya_preapproval_adjustment_rupees_total", "Rupees moved when a bill was reconciled with its pre-approval", ["direction"])
LESSONS = Gauge("arogya_lessons", "Playbook lessons by status", ["status"])


@contextmanager
def timed(node: str):
    t = time.perf_counter()
    try:
        yield
    finally:
        NODE_SECONDS.labels(node).observe(time.perf_counter() - t)


def summarize(claims, worksheets, reports) -> dict:
    """Dataset summary (what the original's computeActualClaimsMetrics showed), computed, not hard-coded."""
    by_reason: Count = Count()
    for ws in worksheets:
        for k, v in ws.deductions_by_reason.items():
            by_reason[k] += v
    return {
        "claims": len(claims),
        "claimed": sum((c.claimed_total for c in claims), Decimal(0)),
        "deducted": sum((w.total_deductions for w in worksheets), Decimal(0)),
        "payable": sum((w.final_payable_amount for w in worksheets), Decimal(0)),
        "repudiated": sum(not w.is_admissible for w in worksheets),
        "room_breaches": sum(w.room_rent_breach for w in worksheets),
        "need_senior": sum(w.requires_senior_approval or r.has_fraud_flag for w, r in zip(worksheets, reports)),
        "flagged": sum(r.has_fraud_flag for r in reports),
        "flags_by_type": dict(Count(f.type for r in reports for f in r.flags)),
        "deductions_by_reason": {k: v for k, v in by_reason.items()},
    }
