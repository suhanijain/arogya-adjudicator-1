"""Policy knowledge base: numbered clauses, bill-term normalisation and BM25 retrieval.

Mirrors src/lib/policyKb.ts and src/lib/vectorDb.ts. The original used a hand-made 64-dimension
"embedding"; this uses BM25 (rank_bm25) behind a LangChain retriever, so the same index can sit in any chain.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from rank_bm25 import BM25Okapi

from ..types.adjudication import PolicyClause

DATA = Path(__file__).parent.parent / "data"
CLAUSES = [PolicyClause.model_validate(c) for c in json.loads((DATA / "policy_clauses.json").read_text())]
TERM_MAP: dict[str, dict] = json.loads((DATA / "term_map.json").read_text())


def tokenize(text: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9.]+", " ", text.lower()).split() if len(t) > 1]


def clause_text(c: PolicyClause) -> str:
    # title and tags are repeated so they weigh more than body text (a simple field boost)
    return f"{c.clause_number} {c.title} {c.title} {' '.join(c.tags)} {' '.join(c.tags)} {c.text}"


class ClauseRetriever(BaseRetriever):
    """LangChain retriever over the numbered clauses. Returns Documents carrying the clause number."""
    k: int = 4
    min_score: float = 0.5

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None) -> list[Document]:
        index = _index()
        scores = index.get_scores(tokenize(query))
        ranked = sorted(zip(CLAUSES, scores), key=lambda p: p[1], reverse=True)[: self.k]
        return [Document(page_content=c.text, metadata={"clause": c.clause_number, "title": c.title, "rule_type": c.rule_type, "score": round(float(s), 2)})
                for c, s in ranked if s >= self.min_score]


@lru_cache
def _index() -> BM25Okapi:
    return BM25Okapi([tokenize(clause_text(c)) for c in CLAUSES])


def search_clauses(query: str, k: int = 4) -> list[Document]:
    return ClauseRetriever(k=k).invoke(query)


def get_clause(number: str) -> PolicyClause | None:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return next((c for c in CLAUSES if norm(number) == norm(c.clause_number) or norm(number) in norm(c.id)), None)


def normalize_bill_term(term: str) -> dict:
    """Map a hospital's wording to a standard head, category and clause (the original's Requirement 1)."""
    t = term.lower().strip()
    for key, m in TERM_MAP.items():
        if key in t or t in key:
            return m
    return {"normalized": term, "category": "other", "primaryClause": "Clause 4.1.d"}
