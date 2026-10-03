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
from app.organization.verification_repository import VerificationRepository
from app.organization.verification_models import VerificationStatus
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
        conn.execute("DELETE FROM operation_verification_history")
        conn.execute("DELETE FROM operation_verifications")
        conn.execute("DELETE FROM operation_executions")
        conn.execute("DELETE FROM operation_approval_history")
        conn.execute("DELETE FROM operation_approvals")
        conn.execute("DELETE FROM organization_operations")
        conn.execute("DELETE FROM organization_plans")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM scans")
        conn.execute("DELETE FROM protected_paths")

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
            {"type": "MOVE", "source": "source.txt", "destination": "dest.txt", "confidence": "HIGH"},
            {"type": "CREATE_FOLDER", "source": "new_folder", "destination": "new_folder", "confidence": "HIGH"},
            {"type": "RENAME", "source": "dir1", "destination": "dir2", "confidence": "HIGH"},
        ]
    )

    with get_connection() as conn:
        cursor = conn.execute("SELECT id FROM organization_operations WHERE plan_id = ? ORDER BY id", (plan_id,))
        op_ids = [row[0] for row in cursor.fetchall()]

    return scan_id, plan_id, op_ids

def _execute_op(op_id, plan_id):
    ApprovalRepository.record_decision(op_id, plan_id, ApprovalDecision.APPROVED, None)
    ExecutionRepository.lock_operation_for_execution(op_id)
    ExecutionRepository.mark_operation_completed(op_id)


# ---------------------------------------------------------------------------
# Requirement 1: Read-only behavior
# ---------------------------------------------------------------------------

class TestReadOnlyBehavior:
    def test_verification_does_not_mutate_filesystem(self, authorized_root, clean_service):
        """Snapshot dedicated test directory before and after verification,
        including failed and inconclusive cases, proving no mutation."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        op_create = op_ids[1]

        _execute_op(op_move, plan_id)
        _execute_op(op_create, plan_id)

        # Set up a filesystem state that will trigger FAILED verification
        # (source still present, dest missing for move; folder missing for create)
        (authorized_root / "source.txt").write_bytes(b"content")

        # Snapshot before
        before = set()
        for p in authorized_root.rglob("*"):
            rel = p.relative_to(authorized_root)
            content = p.read_bytes() if p.is_file() else None
            before.add((str(rel), p.is_file(), content))

        resp = client.post(f"/api/plans/{plan_id}/verify")
        assert resp.status_code == 200

        # Snapshot after
        after = set()
        for p in authorized_root.rglob("*"):
            rel = p.relative_to(authorized_root)
            content = p.read_bytes() if p.is_file() else None
            after.add((str(rel), p.is_file(), content))

        assert before == after, "Verification mutated the filesystem"

    def test_verification_does_not_change_execution_status(self, authorized_root, clean_service):
        """Confirm a FAILED verification does not alter execution state."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)

        # Make verification fail
        (authorized_root / "source.txt").write_bytes(b"content")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        assert resp.status_code == 200

        exec_state = ExecutionRepository.get_operation_execution(op_move)
        assert exec_state.status == ExecutionStatus.COMPLETED

    def test_verification_does_not_change_approval_status(self, authorized_root, clean_service):
        """Confirm verification does not alter approval decisions."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)
        (authorized_root / "source.txt").write_bytes(b"content")

        approval_before = ApprovalRepository.get_approval_for_operation(op_move)

        resp = client.post(f"/api/plans/{plan_id}/verify")
        assert resp.status_code == 200

        approval_after = ApprovalRepository.get_approval_for_operation(op_move)
        assert approval_before["decision"] == approval_after["decision"]


# ---------------------------------------------------------------------------
# Requirement 2: Repeat verification and persistence
# ---------------------------------------------------------------------------

class TestRepeatVerification:
    def test_repeated_verify_no_duplicate_current_rows(self, authorized_root, clean_service):
        """Repeated verification produces exactly one current-state row per operation."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)
        (authorized_root / "dest.txt").write_bytes(b"content")

        client.post(f"/api/plans/{plan_id}/verify")
        client.post(f"/api/plans/{plan_id}/verify")

        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM operation_verifications WHERE operation_id = ?",
                (op_move,)
            )
            assert cursor.fetchone()[0] == 1

    def test_repeated_verify_appends_history(self, authorized_root, clean_service):
        """Each verification appends a row to the immutable history table."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)
        (authorized_root / "dest.txt").write_bytes(b"content")

        client.post(f"/api/plans/{plan_id}/verify")
        client.post(f"/api/plans/{plan_id}/verify")
        client.post(f"/api/plans/{plan_id}/verify")

        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM operation_verification_history WHERE operation_id = ?",
                (op_move,)
            )
            # 3 plan-level verifications × 1 completed op = 3 history rows
            assert cursor.fetchone()[0] == 3

    def test_reverify_after_filesystem_change(self, authorized_root, clean_service):
        """Verification result updates when filesystem state changes between runs."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)

        # First verify: source still exists → FAILED
        (authorized_root / "source.txt").write_bytes(b"content")
        resp1 = client.post(f"/api/plans/{plan_id}/verify")
        v1 = next(v for v in resp1.json()["verifications"] if v["operation_id"] == op_move)
        assert v1["status"] == "FAILED"
        ts1 = v1["verified_at"]

        # Fix filesystem: remove source, create dest → VERIFIED
        (authorized_root / "source.txt").unlink()
        (authorized_root / "dest.txt").write_bytes(b"content")
        resp2 = client.post(f"/api/plans/{plan_id}/verify")
        v2 = next(v for v in resp2.json()["verifications"] if v["operation_id"] == op_move)
        assert v2["status"] == "VERIFIED"
        ts2 = v2["verified_at"]

        # Timestamp must differ to prove it's a fresh verification
        assert ts2 >= ts1

        # History must contain both the FAILED and the VERIFIED records
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT status FROM operation_verification_history WHERE operation_id = ? ORDER BY id",
                (op_move,)
            )
            statuses = [row[0] for row in cursor.fetchall()]
            assert "FAILED" in statuses
            assert "VERIFIED" in statuses

    def test_plan_aggregation_mixed_outcomes(self, authorized_root, clean_service):
        """Plan-level aggregation handles missing verifications and mixed outcomes."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move, op_create, op_rename = op_ids

        _execute_op(op_move, plan_id)
        _execute_op(op_create, plan_id)
        # op_rename is NOT executed → will be INCONCLUSIVE

        (authorized_root / "dest.txt").write_bytes(b"content")
        # new_folder not created → CREATE_FOLDER will FAIL

        resp = client.post(f"/api/plans/{plan_id}/verify")
        data = resp.json()

        assert data["total_operations"] == 3
        assert data["verified_count"] == 1   # MOVE succeeds
        assert data["failed_count"] == 1     # CREATE_FOLDER fails
        assert data["inconclusive_count"] == 1  # RENAME not completed

    def test_get_verifications_before_any_verify(self, authorized_root, clean_service):
        """GET verifications returns correct unverified counts when no verification has run."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)

        resp = client.get(f"/api/plans/{plan_id}/verifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_operations"] == 3
        assert data["verified_count"] == 0
        assert data["unverified_count"] == 3
        assert len(data["verifications"]) == 0


# ---------------------------------------------------------------------------
# Requirement 3: Path and protection safety
# ---------------------------------------------------------------------------

class TestPathSafety:
    def test_traversal_destination_inconclusive(self, authorized_root, clean_service):
        """Out-of-root destination via traversal is safely rejected as INCONCLUSIVE."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)

        with get_connection() as conn:
            conn.execute(
                "UPDATE organization_operations SET destination = '../outside.txt' WHERE id = ?",
                (op_move,)
            )

        resp = client.post(f"/api/plans/{plan_id}/verify")
        data = resp.json()
        v = next(v for v in data["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "INCONCLUSIVE"
        assert "validation failed" in v["details"].lower() or "Destination validation failed" in v["details"]

    def test_traversal_source_inconclusive(self, authorized_root, clean_service):
        """Out-of-root source via traversal is safely rejected as INCONCLUSIVE."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)

        with get_connection() as conn:
            conn.execute(
                "UPDATE organization_operations SET source = '../escape.txt' WHERE id = ?",
                (op_move,)
            )

        resp = client.post(f"/api/plans/{plan_id}/verify")
        data = resp.json()
        v = next(v for v in data["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "INCONCLUSIVE"
        assert "Source validation failed" in v["details"]

    def test_protected_destination_inconclusive(self, authorized_root, clean_service):
        """Protected destination is INCONCLUSIVE, not VERIFIED or FAILED."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)
        (authorized_root / "dest.txt").write_bytes(b"content")
        ProtectedPathService(clean_service).add_protected_path("dest.txt", "system")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        data = resp.json()
        v = next(v for v in data["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "INCONCLUSIVE"
        assert "protected" in v["details"].lower()

    def test_protected_source_inconclusive(self, authorized_root, clean_service):
        """Protected source path is INCONCLUSIVE."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)
        ProtectedPathService(clean_service).add_protected_path("source.txt", "system")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        data = resp.json()
        v = next(v for v in data["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "INCONCLUSIVE"
        assert "protected" in v["details"].lower()

    def test_no_absolute_paths_in_response(self, authorized_root, clean_service):
        """API responses must not contain the authorized root absolute path."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        _execute_op(op_move, plan_id)

        with get_connection() as conn:
            conn.execute(
                "UPDATE organization_operations SET destination = '../escape.txt' WHERE id = ?",
                (op_move,)
            )

        resp = client.post(f"/api/plans/{plan_id}/verify")
        body_text = resp.text
        abs_root = str(authorized_root.resolve())
        assert abs_root not in body_text, f"Absolute path leaked in response: {abs_root}"


# ---------------------------------------------------------------------------
# Requirement 4: Operation postconditions
# ---------------------------------------------------------------------------

class TestOperationPostconditions:
    def test_move_verified_source_absent_dest_present(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        _execute_op(op_move, plan_id)
        (authorized_root / "dest.txt").write_bytes(b"content")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "VERIFIED"

    def test_move_failed_source_still_present(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        _execute_op(op_move, plan_id)
        (authorized_root / "source.txt").write_bytes(b"content")
        (authorized_root / "dest.txt").write_bytes(b"content")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "FAILED"
        assert "Source path still exists" in v["details"]

    def test_move_failed_dest_missing(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        _execute_op(op_move, plan_id)
        # Neither source nor dest exist

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "FAILED"
        assert "Destination path does not exist" in v["details"]

    def test_rename_verified(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_rename = op_ids[2]
        _execute_op(op_rename, plan_id)
        (authorized_root / "dir2").mkdir()

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_rename)
        assert v["status"] == "VERIFIED"

    def test_create_folder_verified(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_create = op_ids[1]
        _execute_op(op_create, plan_id)
        (authorized_root / "new_folder").mkdir()

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_create)
        assert v["status"] == "VERIFIED"

    def test_create_folder_failed_missing(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_create = op_ids[1]
        _execute_op(op_create, plan_id)

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_create)
        assert v["status"] == "FAILED"
        assert "Destination folder does not exist" in v["details"]

    def test_create_folder_wrong_type(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_create = op_ids[1]
        _execute_op(op_create, plan_id)
        (authorized_root / "new_folder").write_bytes(b"content")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_create)
        assert v["status"] == "FAILED"
        assert "not a folder" in v["details"]

    def test_uncompleted_op_inconclusive(self, authorized_root, clean_service):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]
        # NOT executed — stays PENDING

        resp = client.post(f"/api/plans/{plan_id}/verify")
        v = next(v for v in resp.json()["verifications"] if v["operation_id"] == op_move)
        assert v["status"] == "INCONCLUSIVE"
        assert "not COMPLETED" in v["details"]


# ---------------------------------------------------------------------------
# Requirement 5: API behavior
# ---------------------------------------------------------------------------

class TestAPIBehavior:
    def test_verify_without_root_returns_403(self, clean_service):
        """POST /verify without authorized root returns 403."""
        resp = client.post("/api/plans/fake_plan/verify")
        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["code"] == "ROOT_NOT_CONFIGURED"

    def test_verify_missing_plan_returns_404(self, authorized_root, clean_service):
        """POST /verify for nonexistent plan returns 404."""
        resp = client.post("/api/plans/nonexistent_plan/verify")
        assert resp.status_code == 404

    def test_verify_cannot_execute_operations(self, authorized_root, clean_service):
        """Verify endpoint does not execute any pending operations."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_move = op_ids[0]

        # Approve but do NOT execute
        ApprovalRepository.record_decision(op_move, plan_id, ApprovalDecision.APPROVED, None)

        # Create source so if execution happened it would succeed
        (authorized_root / "source.txt").write_bytes(b"content")

        resp = client.post(f"/api/plans/{plan_id}/verify")
        assert resp.status_code == 200

        # File should still be at source — no move happened
        assert (authorized_root / "source.txt").exists()
        assert not (authorized_root / "dest.txt").exists()

        exec_state = ExecutionRepository.get_operation_execution(op_move)
        assert exec_state.status == ExecutionStatus.PENDING
