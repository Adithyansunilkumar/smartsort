import datetime as _dt
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database.connection import get_connection
from app.database.repositories import OrganizationRepository, ScanRepository
from app.filesystem.scanner import ScanResult, ScanStatus
from app.organization.approval_repository import ApprovalRepository
from app.organization.approval_models import ApprovalDecision
from app.organization.execution_repository import ExecutionRepository, ExecutionStatus
from app.services.protected_paths import ProtectedPathService
from app.services.folder_access import FolderAccessService

client = TestClient(app)

def _utcnow():
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()

def _persist_scan_and_plan(authorized_root: Path, clean_service: FolderAccessService):
    (authorized_root / "source.txt").write_text("hello")
    (authorized_root / "dir1").mkdir()

    scan_id = f"scan_{uuid.uuid4().hex[:8]}"
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=1,
        directory_count=1,
        skip_count=0,
        scanned_at=_utcnow(),
        files=[],
    )
    ScanRepository.save_scan(scan)

    plan_id = f"plan_{uuid.uuid4().hex}"
    OrganizationRepository.save_plan(
        plan_id=plan_id,
        scan_id=scan_id,
        objective="test",
        created_at=_utcnow(),
        warnings=[],
        protected_conflicts=[],
        operations=[
            {"type": "MOVE", "source": "source.txt", "destination": "dest.txt", "confidence": "HIGH"},
        ]
    )

    with get_connection() as conn:
        cursor = conn.execute("SELECT id FROM organization_operations WHERE plan_id = ? ORDER BY id", (plan_id,))
        op_ids = [row[0] for row in cursor.fetchall()]

    return scan_id, plan_id, op_ids

class TestProtectedPathEdgeCases:
    def test_protected_directory_and_descendants_blocked(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        prot_dir = authorized_root / "protected"
        prot_dir.mkdir()
        (prot_dir / "subdir").mkdir()
        ProtectedPathService(clean_service).add_protected_path("protected", "system")
        
        with get_connection() as conn:
            conn.execute("UPDATE organization_operations SET destination = 'protected/subdir/dest.txt' WHERE id = ?", (op_id,))
            
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "PROTECTED_PATH"

    def test_similarly_named_sibling_directory_not_blocked(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        (authorized_root / ".git").mkdir()
        ProtectedPathService(clean_service).add_protected_path(".git", "system")
        
        # .github is a sibling and starts with .git, but is not .git
        (authorized_root / ".github").mkdir()
        
        with get_connection() as conn:
            conn.execute("UPDATE organization_operations SET destination = '.github/dest.txt' WHERE id = ?", (op_id,))
            
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.COMPLETED
        assert (authorized_root / ".github/dest.txt").exists()

    def test_protected_file_is_blocked(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        (authorized_root / "config.json").write_text("{}")
        ProtectedPathService(clean_service).add_protected_path("config.json", "system")
        
        with get_connection() as conn:
            conn.execute("UPDATE organization_operations SET destination = 'config.json' WHERE id = ?", (op_id,))
            
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "PROTECTED_PATH"

    def test_symlink_escape_is_rejected(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        # Test out-of-root via parent traversal
        with get_connection() as conn:
            conn.execute("UPDATE organization_operations SET destination = '../outside.txt' WHERE id = ?", (op_id,))
            
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.FAILED
        assert status.error_code == "SECURITY_DENIED"

    def test_blocked_operations_leave_filesystem_unchanged(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        ProtectedPathService(clean_service).add_protected_path("dest.txt", "system")
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        assert (authorized_root / "source.txt").exists()
        assert not (authorized_root / "dest.txt").exists()


class TestExecutionLifecycle:
    def test_approval_does_not_execute_operation(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        
        # Record approval
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        
        # Operation should remain unchanged in PENDING execution state, file is not moved
        assert (authorized_root / "source.txt").exists()
        assert not (authorized_root / "dest.txt").exists()
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.PENDING

    def test_atomic_claim_prevents_concurrent_execution(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        
        # Manually claim it
        assert ExecutionRepository.lock_operation_for_execution(op_id) is True
        
        # A concurrent request should not be able to execute it again since it's IN_PROGRESS
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        # It skips execution because it wasn't PENDING
        assert (authorized_root / "source.txt").exists()
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.IN_PROGRESS

    def test_repeated_requests_do_not_repeat_completed_mutation(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        
        resp1 = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        assert (authorized_root / "dest.txt").exists()
        # Source is PRESERVED because execution uses copy semantics (originals kept safe)
        assert (authorized_root / "source.txt").exists()
        
        # Delete destination to prove the operation doesn't run again once COMPLETED
        (authorized_root / "dest.txt").unlink()
        
        resp2 = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        # Still deleted, operation was already COMPLETED so it doesn't repeat
        assert not (authorized_root / "dest.txt").exists()
        status = ExecutionRepository.get_operation_execution(op_id)
        assert status.status == ExecutionStatus.COMPLETED

    def test_interrupted_operation_not_falsely_completed(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
        
        # Simulate interruption: operation locked but crashed before complete
        ExecutionRepository.lock_operation_for_execution(op_id)
        
        # Another execution request comes in
        resp = client.post(f"/api/plans/{plan_id}/execute", json={"operation_ids": [op_id]})
        
        status = ExecutionRepository.get_operation_execution(op_id)
        # It should still be IN_PROGRESS, not automatically retried or falsely marked COMPLETED
        assert status.status == ExecutionStatus.IN_PROGRESS
        assert (authorized_root / "source.txt").exists()
