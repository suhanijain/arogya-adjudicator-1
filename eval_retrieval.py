"""Retrieval quality: BM25 vs vector database vs hybrid, on paraphrased questions with known clauses.
Run: python eval_retrieval.py     (recall@3 = a correct clause is in the top 3; MRR = how high the first correct one ranks)"""
import json
from pathlib import Path

from arogya.lib import vector_db as v

SETS = {n: json.loads((Path(__file__).parent / f"arogya/data/{n}.json").read_text()) for n in ("retrieval_eval", "retrieval_heldout")}
MODES = {"bm25": lambda q: v.bm25_clauses(q, 3), "vector": lambda q: v.semantic_clauses(q, 3, min_sim=0.0), "hybrid": lambda q: v.hybrid_clauses(q, 3)}


def evaluate(name: str = "retrieval_eval") -> dict:
    CASES, out = SETS[name], {}
    for name, fn in MODES.items():
        hits, rr, misses = 0, 0.0, []
        for c in CASES:
            got = [d.metadata["clause"] for d in fn(c["query"])]
            rank = next((i + 1 for i, g in enumerate(got) if g in c["expected"]), None)
            hits += rank is not None
            rr += 1 / rank if rank else 0
            if rank is None:
                misses.append(c["query"])
        out[name] = {"recall@3": round(hits / len(CASES), 3), "mrr": round(rr / len(CASES), 3), "misses": misses}
    return out


if __name__ == "__main__":
    print("embedder:", v.embedder_name())
    for name in SETS:
        print(f"\n{name} ({len(SETS[name])} queries)" + ("  <- used to choose the weights" if name == "retrieval_eval" else "  <- written afterwards, never tuned on"))
        for k, r in evaluate(name).items():
            print(f"  {k:7s} recall@3 {r['recall@3']:.2f}  MRR {r['mrr']:.2f}  missed {len(r['misses'])}" + (f"  {r['misses']}" if name == "retrieval_heldout" and r["misses"] else ""))
