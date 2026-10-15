import pytest
import uuid
import os
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.database.schema import init_db
from app.database.connection import get_connection
from app.database.repositories import ScanRepository, OrganizationRepository
from app.filesystem.scanner import ScanResult, ScanStatus, ScannedFile
from app.organization.approval_repository import ApprovalRepository
from app.organization.approval_models import ApprovalDecision
from app.organization.execution_repository import ExecutionRepository
from app.organization.execution_models import ExecutionStatus
from app.services.protected_paths import ProtectedPathService

client = TestClient(app)

def _utcnow():
    import datetime as _dt
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()

def _make_plan_id():
    return f"plan_{uuid.uuid4().hex}"

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM operation_executions")
        conn.execute("DELETE FROM operation_approval_history")
        conn.execute("DELETE FROM operation_approvals")
        conn.execute("DELETE FROM organization_operations")
        conn.execute("DELETE FROM organization_plans")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM scans")

@pytest.fixture
def clean_service():
    svc = get_folder_access_service()
    svc.clear_authorized_root()
    return svc

@pytest.fixture
def authorized_root(tmp_path, clean_service):
    root = tmp_path / "authorized_root"
    root.mkdir()
    clean_service.set_authorized_root(root)
    return root

def _persist_scan_and_plan(root: Path, service: FolderAccessService):
    (root / "source.txt").write_bytes(b"content")
    (root / "dir1").mkdir(exist_ok=True)
    
    scan_id = f"scan_{uuid.uuid4().hex[:8]}"
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=1,
        skip_count=0,
        scanned_at=_utcnow(),
        files=[ScannedFile(name="source.txt", relative_path="source.txt", extension=".txt", size_bytes=7, modified_at=_utcnow())],
        directories=[{"name": "dir1", "relative_path": "dir1"}],
    )
    ScanRepository.save_scan(scan)

    plan_id = _make_plan_id()
    OrganizationRepository.save_plan(
        plan_id=plan_id,
        scan_id=scan_id,
        objective=None,
        created_at=_utcnow(),
        warnings=[],
        protected_conflicts=[],
        operations=[
            {"type": "MOVE", "source": "source.txt", "destination": "SmartSort_Organized/moved.txt", "confidence": "HIGH"},
            {"type": "CREATE_FOLDER", "source": "new_folder", "destination": "SmartSort_Organized/new_folder", "confidence": "HIGH"},
            {"type": "RENAME", "source": "dir1", "destination": "SmartSort_Organized/dir2", "confidence": "HIGH"},
        ]
    )
    
    with get_connection() as conn:
        cursor = conn.execute("SELECT id FROM organization_operations WHERE plan_id = ? ORDER BY id", (plan_id,))
        op_ids = [row[0] for row in cursor.fetchall()]
        
    return scan_id, plan_id, op_ids

class TestExecutionValidation:
    def test_execute_approved_move(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        ApprovalRepository.record_decision(op_move, plan_id, ApprovalDecision.APPROVED, None)
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_move]})
        assert resp.status_code == 200
        
        status = ExecutionRepository.get_operation_execution(op_move)
        print(f"Status: {status.status}, Error: {status.error_code} - {status.error_message}")
        
        # Verify file was COPIED (originals preserved)
        assert (authorized_root / "source.txt").exists()
        assert (authorized_root / "SmartSort_Organized" / "moved.txt").exists()
        
        assert status.status == ExecutionStatus.COMPLETED

    def test_unapproved_skipped(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        # Left as PENDING
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_move]})
        assert resp.status_code == 200
        
        # File not moved
        assert (authorized_root / "source.txt").exists()
        
        status = ExecutionRepository.get_operation_execution(op_move)
        assert status.status == ExecutionStatus.SKIPPED
        assert status.error_code == "UNAPPROVED"

    def test_destination_conflict_fails(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        ApprovalRepository.record_decision(op_move, plan_id, ApprovalDecision.APPROVED, None)
        
        # Create conflict inside the organized folder
        org_folder = authorized_root / "SmartSort_Organized"
        org_folder.mkdir(exist_ok=True)
        (org_folder / "moved.txt").write_bytes(b"conflict")
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_move]})
        assert resp.status_code == 200
        
        status = ExecutionRepository.get_operation_execution(op_move)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "DESTINATION_CONFLICT"

    def test_missing_source_fails(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        ApprovalRepository.record_decision(op_move, plan_id, ApprovalDecision.APPROVED, None)
        
        # Remove source
        (authorized_root / "source.txt").unlink()
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_move]})
        assert resp.status_code == 200
        
        status = ExecutionRepository.get_operation_execution(op_move)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "SOURCE_MISSING"

    def test_protected_destination_fails(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        
        # We need to change the DB operation destination to a protected path
        with get_connection() as conn:
            conn.execute("UPDATE organization_operations SET destination = '.git/config' WHERE id = ?", (op_move,))
        
        (authorized_root / ".git").mkdir()
        ProtectedPathService(clean_service).add_protected_path(".git", "system")
        
        ApprovalRepository.record_decision(op_move, plan_id, ApprovalDecision.APPROVED, None)
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_move]})
        
        status = ExecutionRepository.get_operation_execution(op_move)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "PROTECTED_PATH"

    def test_create_folder(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_create = op_ids[1]
        ApprovalRepository.record_decision(op_create, plan_id, ApprovalDecision.APPROVED, None)
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_create]})
        assert resp.status_code == 200
        
        assert (authorized_root / "SmartSort_Organized" / "new_folder").is_dir()
        
        status = ExecutionRepository.get_operation_execution(op_create)
        assert status.status == ExecutionStatus.COMPLETED

    def test_rename_folder(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_rename = op_ids[2]
        ApprovalRepository.record_decision(op_rename, plan_id, ApprovalDecision.APPROVED, None)
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_rename]})
        assert resp.status_code == 200
        
        # Rename behaves as copy for directories as well
        assert (authorized_root / "dir1").exists()
        assert (authorized_root / "SmartSort_Organized" / "dir2").is_dir()
        
        status = ExecutionRepository.get_operation_execution(op_rename)
        assert status.status == ExecutionStatus.COMPLETED

class TestExecutionAPI:
    def test_get_executions(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.get(f"/api/plans/{plan_id}/executions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_operations"] == 3
        assert data["pending_count"] == 3
        assert data["completed_count"] == 0

    def test_execute_without_root_fails(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        clean_service.clear_authorized_root()
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": op_ids})
        assert resp.status_code == 403
        assert "ROOT_NOT_CONFIGURED" in resp.json()["detail"]["code"]
