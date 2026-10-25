import pytest
from fastapi.testclient import TestClient

import eval_retrieval
import server
from arogya.lib import vector_db as v
from arogya.lib.policy_kb import CLAUSES

api = TestClient(server.app, headers={"X-Adjudicator-Key": "demo-key"})


def top(q, mode="vector", k=3):
    return [d.metadata["clause"] for d in {"vector": lambda: v.semantic_clauses(q, k), "bm25": lambda: v.bm25_clauses(q, k), "hybrid": lambda: v.hybrid_clauses(q, k)}[mode]()]


def test_vector_database_is_built_persisted_and_not_duplicated():
    st = v.stats()
    assert st["clauses"] == len(CLAUSES) == 33 and st["terms"] == 22 and "Chroma" in st["store"]
    v.clause_store.cache_clear()
    assert v.clause_store()._collection.count() == len(CLAUSES)            # reopened from disk, not added twice


def test_embeddings_are_dense_unit_vectors_and_paraphrases_land_close():
    e = v.embedder()[0]
    a, b, c = e.embed_query("cost of a hospital bed each night"), e.embed_query("room rent per day"), e.embed_query("forged documents and lying")
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))
    assert len(a) > 20 and abs(dot(a, a) - 1) < 1e-6 and dot(a, b) > dot(a, c)


@pytest.mark.parametrize("q,expect", [("fake or forged claim consequences", "Clause 10.9"), ("robot assisted surgery maximum amount", "Clause 4.6"), ("eye lens surgery limit per eye", "Clause 4.3")])
def test_semantic_search_finds_clauses_without_shared_keywords(q, expect):
    assert expect in top(q)


def test_hybrid_reports_where_each_hit_came_from_and_gibberish_finds_nothing():
    hits = v.hybrid_clauses("room rent cap per day", 3)
    assert hits and all(h.metadata["mode"] for h in hits) and any("vector" in h.metadata["mode"] for h in hits)
    assert v.semantic_clauses("zzzz qqqq") == [] and v.bm25_clauses("zzzz qqqq") == []


def test_changing_the_embedder_builds_a_new_collection_and_removes_the_old(monkeypatch):
    before = {c.name for c in v.clause_store()._client.list_collections()}
    monkeypatch.setattr(v, "embedder_name", lambda: "other-model")
    v.clause_store.cache_clear()
    st = v.clause_store()
    names = {c.name for c in st._client.list_collections() if c.name.startswith("clauses_")}
    assert len(names) == 1 and names.isdisjoint({n for n in before if n.startswith("clauses_")})
    monkeypatch.undo()
    v.clause_store.cache_clear()


def test_similar_claims_and_term_normalisation():
    rows = api.get("/api/adjudicator/claims/CLM-001/similar").json()
    assert len(rows) == 5 and all(r["claim"] != "CLM-001" for r in rows)
    assert rows[0]["diagnosis"] and "hernia" in (rows[0]["diagnosis"] + rows[0]["procedure"]).lower()
    assert api.get("/api/policy/normalize", params={"term": "Room Rent"}).json()["via"] == "text"


def test_search_endpoint_modes_and_info():
    assert api.get("/api/policy/search", params={"q": "ambulance transport", "mode": "bm25"}).json()
    assert api.get("/api/policy/search", params={"q": "x", "mode": "nope"}).status_code == 400
    assert api.get("/api/policy/vector-db").json()["embedder"]


def test_retrieval_quality_is_measured_not_assumed():
    dev, held = eval_retrieval.evaluate("retrieval_eval"), eval_retrieval.evaluate("retrieval_heldout")
    assert dev["vector"]["recall@3"] >= dev["bm25"]["recall@3"]
    assert held["vector"]["recall@3"] >= held["bm25"]["recall@3"] and held["vector"]["recall@3"] >= 0.75
