"""API tests for Phase 9 Agent Controller endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database.schema import init_db
from app.services.folder_access import get_folder_access_service, FolderAccessService


@pytest.fixture
def client_env(tmp_path):
    db_file = tmp_path / "test_agent_api.db"
    init_db(db_file)
    from app.database import connection
    orig_path = connection.DEFAULT_DB_PATH
    connection.DEFAULT_DB_PATH = db_file

    auth_dir = tmp_path / "authorized_root"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "file1.txt").write_text("Test content 1", encoding="utf-8")
    (auth_dir / "file2.py").write_text("print('test')", encoding="utf-8")

    folder_service = FolderAccessService()
    app.dependency_overrides[get_folder_access_service] = lambda: folder_service

    client = TestClient(app)

    yield client, folder_service, auth_dir

    app.dependency_overrides.clear()
    connection.DEFAULT_DB_PATH = orig_path


class TestAgentAPI:
    """Test /api/agent endpoints."""

    def test_start_run_no_root_fails(self, client_env):
        client, folder_service, auth_dir = client_env
        # Root not configured
        resp = client.post("/api/agent/runs", json={"objective": "Organize"})
        assert resp.status_code == 409
        data = resp.json()
        assert data["detail"]["code"] == "ROOT_NOT_CONFIGURED"

    def test_start_run_empty_objective_fails(self, client_env):
        client, folder_service, auth_dir = client_env
        folder_service.set_authorized_root(auth_dir)

        # Empty objective
        resp = client.post("/api/agent/runs", json={"objective": ""})
        assert resp.status_code == 422

        # Whitespace objective
        resp2 = client.post("/api/agent/runs", json={"objective": "   "})
        assert resp2.status_code == 422

    def test_start_run_success(self, client_env):
        client, folder_service, auth_dir = client_env
        folder_service.set_authorized_root(auth_dir)

        resp = client.post("/api/agent/runs", json={"objective": "Sort by file type"})
        assert resp.status_code == 201
        data = resp.json()
        assert "run" in data
        run = data["run"]
        assert run["state"] == "WAITING_FOR_APPROVAL"
        assert run["scan_id"] is not None
        assert run["plan_id"] is not None
        assert run["progress"] == 1.0

        # Retrieve status
        run_id = run["run_id"]
        get_resp = client.get(f"/api/agent/runs/{run_id}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["run"]["run_id"] == run_id
        assert get_data["run"]["state"] == "WAITING_FOR_APPROVAL"

    def test_get_unknown_run_404(self, client_env):
        client, folder_service, auth_dir = client_env
        resp = client.get("/api/agent/runs/run_nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RUN_NOT_FOUND"

    def test_cancel_run_api(self, client_env):
        client, folder_service, auth_dir = client_env
        folder_service.set_authorized_root(auth_dir)

        start_resp = client.post("/api/agent/runs", json={"objective": "Organize documents"})
        run_id = start_resp.json()["run"]["run_id"]

        cancel_resp = client.post(f"/api/agent/runs/{run_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["run"]["state"] == "CANCELLED"

        # Idempotent cancel
        re_cancel = client.post(f"/api/agent/runs/{run_id}/cancel")
        assert re_cancel.status_code == 200
        assert re_cancel.json()["run"]["state"] == "CANCELLED"

    def test_cancel_unknown_run_404(self, client_env):
        client, folder_service, auth_dir = client_env
        resp = client.post("/api/agent/runs/run_nonexistent/cancel")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RUN_NOT_FOUND"
