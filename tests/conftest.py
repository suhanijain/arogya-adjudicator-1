import os
import tempfile

os.environ.setdefault("VECTOR_DB_DIR", tempfile.mkdtemp(prefix="arogya-vec-"))
import pytest

from arogya.lib import agent_orchestrator as orch
from arogya.lib.claims_db import claims_db
from arogya.lib.claim_preapproval import claim_preapprovals
from arogya.lib.preauth import preauth
from arogya.lib.reflexion_engine import reflexion_engine


@pytest.fixture(autouse=True)
def fresh():
    claims_db.reset(); reflexion_engine.reset(); preauth.reset(); claim_preapprovals.reset()
    orch.graph = orch.build_graph(); orch._threads.clear()
