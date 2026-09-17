from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epipilot.web.app import create_app
from epipilot.web.service import WorkbenchService
from test_web_repository import make_repo


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    service = WorkbenchService(make_repo(tmp_path / "repo"))
    return TestClient(create_app(service), base_url="http://127.0.0.1")


def test_live_repository_has_no_fabricated_execution(client: TestClient) -> None:
    response = client.get("/api/project")
    assert response.status_code == 200
    view = response.json()
    assert view["source_mode"] == "repository"
    assert view["tasks"] == []
    assert view["event_version"] == 0
    assert view["acceptance_status"] == "not_assessed"
    assert view["execution_enabled"] is False
    assert view["repository"]["revision"]
    assert "completion_percentage" not in view


def test_report_and_search_are_grounded(client: TestClient) -> None:
    response = client.get("/api/search", params={"q": "Coordinates"})
    assert response.status_code == 200
    assert response.json()["results"][0]["refs"]
    report = client.get("/api/report")
    assert report.status_code == 200
    assert "text/markdown" in report.headers["content-type"]
    assert "NOT ASSESSED" in report.text
    assert "Coordinates" in report.text


@pytest.mark.parametrize("backend", ["pi", "codex", "dsh"])
def test_selected_backend_only_changes_exported_proposal(client: TestClient, backend: str) -> None:
    card_id = client.get("/api/project").json()["opportunities"][0]["id"]
    response = client.get(f"/api/proposals/{card_id}", params={"backend": backend})
    assert response.status_code == 200
    proposal = response.json()
    assert proposal["executor"]["backend"] == backend
    assert proposal["status"] == "proposal_only"
    assert proposal["execution_authorized"] is False
    assert proposal["repository_revision"]
    assert client.get("/api/project").json()["tasks"] == []


def test_unknown_backend_rejected(client: TestClient) -> None:
    assert client.get("/api/proposals/missing", params={"backend": "oops"}).status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutations_are_unavailable(client: TestClient, method: str) -> None:
    assert client.request(method, "/api/project").status_code == 405


def test_host_origin_and_cross_site_access_are_rejected(client: TestClient) -> None:
    assert client.get("/api/project", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/project", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/project", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/project", headers={"origin": "http://127.0.0.1"}).status_code == 200


def test_static_security_and_no_arbitrary_file_endpoint(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert "no-store" in page.headers["cache-control"]
    assert "EpiPilot" in page.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/files", params={"path": "/etc/passwd"}).status_code == 404


def test_missing_database_is_explicit_and_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"
    app = create_app(WorkbenchService(make_repo(tmp_path / "repo"), events_db=missing, project_id="p"))
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get("/api/project")
    assert response.status_code == 503
    assert not missing.exists()
    assert str(tmp_path) not in response.text
