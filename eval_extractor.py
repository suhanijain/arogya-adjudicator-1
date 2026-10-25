"""DSPy evaluation of the extractor against ground truth (the synthetic claims' own fields).
Run: python eval_extractor.py   (needs no API key; the rules do the work)"""
import dspy

from arogya.data.synthetic_claims import load_claims
from arogya.lib.document_extractor import ExtractionProgram

examples = [dspy.Example(documents=c.raw_documents.model_dump(), truth=c).with_inputs("documents") for c in load_claims()]


def metric(ex, pred, trace=None) -> float:
    t, g = ex.truth, pred.claim
    checks = [t.claim_id == g.claim_id, t.claimed_total == g.claimed_total, t.diagnosis == g.diagnosis, t.hospital_name == g.hospital_name,
              [(i.category, i.claimed_amount) for i in t.line_items] == [(i.category, i.claimed_amount) for i in g.line_items]]
    return sum(checks) / len(checks)


if __name__ == "__main__":
    r = dspy.Evaluate(devset=examples, metric=metric, display_progress=False, display_table=0)(ExtractionProgram())
    print("extractor score:", r.score if hasattr(r, "score") else r)
