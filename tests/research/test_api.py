from fastapi.testclient import TestClient
from test_engine import Runner, Verifier, setup

from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.workspace import api


def test_configured_project_uses_carts_in_existing_loop_endpoint(tmp_path, monkeypatch):
    engine = setup(InMemoryEventStore(), tmp_path)
    monkeypatch.setattr(api, "_carts_engine", lambda project: engine)
    monkeypatch.setattr(api, "_carts_adapter", lambda project: Both())

    class Both(Runner, Verifier):
        def __init__(self):
            Runner.__init__(self)
            Verifier.__init__(self)

    client = TestClient(api.app)
    result = client.post("/api/projects/project/research-agent/loop", json={"max_rounds": 10})
    assert result.status_code == 200
    assert result.json()["rounds_completed"] == 1
    assert client.get("/api/projects/project/research-agent/loop").json()["scheduler"] == "carts"
