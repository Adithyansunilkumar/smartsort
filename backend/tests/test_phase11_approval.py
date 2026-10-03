"""Phase 11 — Comprehensive tests for the Approval System.

Covers:
1. Persistence and history — records are created, history is appended, immutable.
2. Decision changes and idempotency — same decision is a no-op, change adds history.
3. Validation — plan/operation membership, supported types, paths, protection, conflicts.
4. API errors — 404 for unknown plan/op, 422 for PENDING client decision, ineligible ops.
5. Agent Controller behavior — WAITING_FOR_APPROVAL state is not changed by approvals.
6. Zero filesystem mutation — snapshot before/after approval workflow.

NOTE: The approval system explicitly does NOT include an execute/apply step.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.agent.controller import AgentController
from app.agent.state import AgentState
from app.database.connection import get_connection
from app.database.repositories import OrganizationRepository, ScanRepository
from app.database.schema import init_db
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.main import app
from app.organization.approval_models import ApprovalDecision
from app.organization.approval_repository import ApprovalRepository
from app.organization.approval_service import (
    ApprovalValidationError,
    get_operation_approval_history,
    get_plan_approval_status,
    record_operation_decision,
)
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _make_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex}"


def _persist_scan_and_plan(
    tmp_path: Path,
    folder_service: FolderAccessService,
) -> tuple[str, str, list[int]]:
    """Persist a scan, analysis rows, and an organization plan.

    Returns (scan_id, plan_id, [operation_ids]).
    """
    # Write dummy files so the scanner has something to work with
    (tmp_path / "report.pdf").write_bytes(b"pdf")
    (tmp_path / "photo.jpg").write_bytes(b"jpg")
    
    # Create parent directories for the planned destinations
    # so that FolderAccessService allow_nonexistent=True check passes
    (tmp_path / "Documents").mkdir(exist_ok=True)
    (tmp_path / "Images").mkdir(exist_ok=True)

    scan_id = f"scan_{uuid.uuid4().hex[:8]}"
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=2,
        directory_count=0,
        skip_count=0,
        scanned_at=_utcnow(),
        files=[
            ScannedFile(
                name="report.pdf",
                relative_path="report.pdf",
                extension=".pdf",
                size_bytes=3,
                modified_at=_utcnow(),
            ),
            ScannedFile(
                name="photo.jpg",
                relative_path="photo.jpg",
                extension=".jpg",
                size_bytes=3,
                modified_at=_utcnow(),
            ),
        ],
    )
    ScanRepository.save_scan(scan)

    # Insert analysis rows so the planner can categorize
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO file_analysis (file_id, version, category, size_bytes, size_bucket, "
            "age_days, age_bucket, depth, analyzed_at, source_size, source_mtime) "
            "VALUES (1, 1, 'document', 3, 'small', 1, 'recent', 1, ?, 3, ?)",
            (_utcnow(), _utcnow()),
        )
        conn.execute(
            "INSERT INTO file_analysis (file_id, version, category, size_bytes, size_bucket, "
            "age_days, age_bucket, depth, analyzed_at, source_size, source_mtime) "
            "VALUES (2, 1, 'image', 3, 'small', 1, 'recent', 1, ?, 3, ?)",
            (_utcnow(), _utcnow()),
        )

    plan_id = _make_plan_id()
    operations = [
        {
            "type": "MOVE",
            "source": "report.pdf",
            "destination": "Documents/report.pdf",
            "reason": "Grouped by category",
            "confidence": "HIGH",
        },
        {
            "type": "MOVE",
            "source": "photo.jpg",
            "destination": "Images/photo.jpg",
            "reason": "Grouped by category",
            "confidence": "HIGH",
        },
    ]
    OrganizationRepository.save_plan(
        plan_id=plan_id,
        scan_id=scan_id,
        objective=None,
        created_at=_utcnow(),
        warnings=[],
        protected_conflicts=[],
        operations=operations,
    )

    # Retrieve the autoincrement IDs
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT id FROM organization_operations WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        )
        op_ids = [row[0] for row in cursor.fetchall()]

    return scan_id, plan_id, op_ids


def snapshot_directory(root: Path) -> dict:
    """Deep snapshot of all files and directories under root."""
    snapshot: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        snapshot[rel_dir] = {"subdirs": sorted(dirnames), "files": sorted(filenames)}
        for f in filenames:
            fp = Path(dirpath) / f
            st = fp.stat()
            snapshot[os.path.relpath(fp, root)] = {
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "hash": hashlib.sha256(fp.read_bytes()).hexdigest(),
            }
    return snapshot


# ===========================================================================
# 1. Persistence and History
# ===========================================================================

class TestApprovalPersistence:
    """Verify that approval records are created and history is maintained."""

    def test_seed_pending_records_on_plan_creation(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Saving a plan automatically seeds PENDING approval rows."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)

        assert len(op_ids) == 2
        for op_id in op_ids:
            record = ApprovalRepository.get_approval_for_operation(op_id)
            assert record is not None, f"PENDING record missing for op {op_id}"
            assert record["decision"] == "PENDING"
            assert record["plan_id"] == plan_id

    def test_initial_history_is_empty(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Newly created approval records have no history entries."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        history = ApprovalRepository.get_approval_history(op_ids[0])
        assert history == [], "History should be empty before any decision"

    def test_history_entry_written_on_first_decision(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Approving an operation appends one history entry."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", "looks good")
        history = ApprovalRepository.get_approval_history(op_id)

        assert len(history) == 1
        entry = history[0]
        assert entry["previous_decision"] == "PENDING"
        assert entry["new_decision"] == "APPROVED"
        assert entry["comment"] == "looks good"

    def test_history_grows_on_decision_change(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Changing a decision appends a second history entry."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)
        ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", "changed mind")
        history = ApprovalRepository.get_approval_history(op_id)

        assert len(history) == 2
        assert history[0]["previous_decision"] == "PENDING"
        assert history[0]["new_decision"] == "APPROVED"
        assert history[1]["previous_decision"] == "APPROVED"
        assert history[1]["new_decision"] == "REJECTED"

    def test_history_is_immutable_after_write(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """History rows cannot be overwritten; each change appends a new entry."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)
        first_history_ids = [r["id"] for r in ApprovalRepository.get_approval_history(op_id)]

        ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", None)
        second_history_ids = [r["id"] for r in ApprovalRepository.get_approval_history(op_id)]

        # The first entry IDs must still be present
        assert first_history_ids[0] in second_history_ids
        assert len(second_history_ids) == 2  # original + new


# ===========================================================================
# 2. Decision Changes and Idempotency
# ===========================================================================

class TestDecisionIdempotency:
    """Verify idempotency and change semantics."""

    def test_same_decision_is_no_op(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """Submitting the same decision twice does not create a new history entry."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)
        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", "duplicate submit")

        history = ApprovalRepository.get_approval_history(op_id)
        assert len(history) == 1, "Duplicate submission must not create a second history entry"
        assert history[0]["new_decision"] == "APPROVED"

    def test_decision_change_updates_current_row(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """After a change, the current approval row reflects the new decision."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)
        ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", None)

        record = ApprovalRepository.get_approval_for_operation(op_id)
        assert record is not None
        assert record["decision"] == "REJECTED"

    def test_rejected_to_approved_change(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """REJECTED → APPROVED is allowed and recorded."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]

        ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", None)
        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", "reconsidered")

        record = ApprovalRepository.get_approval_for_operation(op_id)
        assert record["decision"] == "APPROVED"
        history = ApprovalRepository.get_approval_history(op_id)
        assert len(history) == 2

    def test_pending_is_invalid_client_decision(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """record_decision raises ValueError for PENDING."""
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        with pytest.raises(ValueError, match="PENDING"):
            ApprovalRepository.record_decision(op_ids[0], plan_id, "PENDING", None)


# ===========================================================================
# 3. Service-level Validation
# ===========================================================================

class TestApprovalValidation:
    """Validation tests using the approval service layer."""

    def test_unknown_plan_raises_validation_error(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        with pytest.raises(ApprovalValidationError) as exc_info:
            get_plan_approval_status("plan_nonexistent", clean_service)
        assert exc_info.value.code == "PLAN_NOT_FOUND"

    def test_operation_not_in_plan_raises_validation_error(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, _ = _persist_scan_and_plan(authorized_root, clean_service)
        with pytest.raises(ApprovalValidationError) as exc_info:
            record_operation_decision(plan_id, 99999, ApprovalDecision.APPROVED, None, clean_service)
        assert exc_info.value.code == "OPERATION_NOT_FOUND"

    def test_plan_status_returns_all_operations(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        status = get_plan_approval_status(plan_id, clean_service)

        assert status.plan_id == plan_id
        assert status.total_operations == 2
        assert status.pending_count == 2
        assert status.approved_count == 0
        assert status.rejected_count == 0
        assert len(status.operations) == 2

    def test_protected_path_marks_operation_ineligible(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        """An operation whose source is protected is ineligible."""
        # Create a protected path
        protected_dir = authorized_root / ".git"
        protected_dir.mkdir(exist_ok=True)
        prot_svc = ProtectedPathService(clean_service)
        prot_svc.add_protected_path(".git", "system")

        scan_id = f"scan_{uuid.uuid4().hex[:8]}"
        scan = ScanResult(
            scan_id=scan_id,
            status=ScanStatus.COMPLETED,
            root_display="TestRoot",
            file_count=1,
            directory_count=0,
            skip_count=0,
            scanned_at=_utcnow(),
            files=[ScannedFile(name="f.txt", relative_path="f.txt", extension=".txt", size_bytes=1, modified_at=_utcnow())],
        )
        ScanRepository.save_scan(scan)

        (authorized_root / "backup").mkdir(exist_ok=True)

        plan_id = _make_plan_id()
        OrganizationRepository.save_plan(
            plan_id=plan_id,
            scan_id=scan_id,
            objective=None,
            created_at=_utcnow(),
            warnings=[],
            protected_conflicts=[],
            operations=[{
                "type": "MOVE",
                "source": ".git/config",
                "destination": "backup/config",
                "reason": "test",
                "confidence": "HIGH",
            }],
        )

        status = get_plan_approval_status(plan_id, clean_service)
        op = status.operations[0]
        assert not op.is_eligible
        assert "protected" in (op.ineligibility_reason or "").lower()

    def test_approve_eligible_operation_succeeds(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        updated = record_operation_decision(
            plan_id, op_ids[0], ApprovalDecision.APPROVED, "approved via service", clean_service
        )
        assert updated.approval_decision == ApprovalDecision.APPROVED
        assert updated.is_eligible

    def test_reject_eligible_operation_succeeds(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        updated = record_operation_decision(
            plan_id, op_ids[1], ApprovalDecision.REJECTED, "not needed", clean_service
        )
        assert updated.approval_decision == ApprovalDecision.REJECTED

    def test_plan_status_aggregate_counts_update(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        record_operation_decision(plan_id, op_ids[0], ApprovalDecision.APPROVED, None, clean_service)
        record_operation_decision(plan_id, op_ids[1], ApprovalDecision.REJECTED, None, clean_service)

        status = get_plan_approval_status(plan_id, clean_service)
        assert status.approved_count == 1
        assert status.rejected_count == 1
        assert status.pending_count == 0

    def test_history_service_returns_entries(
        self, authorized_root: Path, clean_service: FolderAccessService
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)
        ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", None)

        entries = get_operation_approval_history(plan_id, op_id)
        assert len(entries) == 2
        assert entries[0].previous_decision == ApprovalDecision.PENDING
        assert entries[1].previous_decision == ApprovalDecision.APPROVED


# ===========================================================================
# 4. API Tests
# ===========================================================================

class TestApprovalAPI:
    """HTTP endpoint tests for the approval API."""

    def test_get_plan_approvals_not_found(self, clean_service, authorized_root):
        resp = client.get("/api/plans/plan_does_not_exist/approvals")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "PLAN_NOT_FOUND"

    def test_get_plan_approvals_returns_all_pending(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.get(f"/api/plans/{plan_id}/approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"] == plan_id
        assert data["total_operations"] == 2
        assert data["pending_count"] == 2
        assert all(op["approval_decision"] == "PENDING" for op in data["operations"])

    def test_post_approve_operation_success(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.post(
            f"/api/plans/{plan_id}/approvals/{op_ids[0]}",
            json={"decision": "APPROVED", "comment": "looks good"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["operation"]["approval_decision"] == "APPROVED"
        assert "No files have been changed" in data["message"]

    def test_post_reject_operation_success(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.post(
            f"/api/plans/{plan_id}/approvals/{op_ids[1]}",
            json={"decision": "REJECTED"},
        )
        assert resp.status_code == 200
        assert resp.json()["operation"]["approval_decision"] == "REJECTED"

    def test_post_pending_decision_rejected_as_client_input(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.post(
            f"/api/plans/{plan_id}/approvals/{op_ids[0]}",
            json={"decision": "PENDING"},
        )
        assert resp.status_code == 422
        assert "PENDING" in resp.json()["detail"]["message"]

    def test_post_unknown_operation_returns_404(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, _ = _persist_scan_and_plan(authorized_root, clean_service)
        resp = client.post(
            f"/api/plans/{plan_id}/approvals/99999",
            json={"decision": "APPROVED"},
        )
        assert resp.status_code == 404

    def test_post_unknown_plan_returns_404(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        resp = client.post(
            "/api/plans/plan_nonexistent/approvals/1",
            json={"decision": "APPROVED"},
        )
        assert resp.status_code == 404

    def test_get_operation_history_returns_entries(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        # Approve then reject
        client.post(f"/api/plans/{plan_id}/approvals/{op_id}", json={"decision": "APPROVED"})
        client.post(f"/api/plans/{plan_id}/approvals/{op_id}", json={"decision": "REJECTED"})

        resp = client.get(f"/api/plans/{plan_id}/approvals/{op_id}/history")
        assert resp.status_code == 200
        history = resp.json()
        assert len(history) == 2
        assert history[0]["previous_decision"] == "PENDING"
        assert history[0]["new_decision"] == "APPROVED"
        assert history[1]["previous_decision"] == "APPROVED"
        assert history[1]["new_decision"] == "REJECTED"

    def test_idempotent_decision_no_extra_history(
        self, clean_service: FolderAccessService, authorized_root: Path
    ):
        _, plan_id, op_ids = _persist_scan_and_plan(authorized_root, clean_service)
        op_id = op_ids[0]
        client.post(f"/api/plans/{plan_id}/approvals/{op_id}", json={"decision": "APPROVED"})
        client.post(f"/api/plans/{plan_id}/approvals/{op_id}", json={"decision": "APPROVED"})

        resp = client.get(f"/api/plans/{plan_id}/approvals/{op_id}/history")
        assert resp.status_code == 200
        assert len(resp.json()) == 1, "Duplicate submission must not add a history entry"


        resp2 = client.post("/api/plans/any/apply", json={})
        assert resp2.status_code == 404


# ===========================================================================
# 5. Agent Controller Integration
# ===========================================================================

def _snapshot_dir(root: Path) -> dict:
    return snapshot_directory(root)


@pytest.fixture
def agent_env(tmp_path):
    """Set up database and authorized root for agent controller tests."""
    db_file = tmp_path / "agent11.db"
    init_db(db_file)
    from app.database import connection
    orig = connection.DEFAULT_DB_PATH
    connection.DEFAULT_DB_PATH = db_file

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "doc.txt").write_text("hello world document", encoding="utf-8")
    (auth_dir / "image.png").write_bytes(b"pngdata")

    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(auth_dir)

    yield folder_svc, auth_dir

    connection.DEFAULT_DB_PATH = orig


class TestAgentControllerWithApproval:
    """Agent Controller must stay in WAITING_FOR_APPROVAL after decisions."""

    def test_approval_does_not_change_agent_state(self, agent_env):
        """Recording approval decisions must not transition the agent run state."""
        folder_svc, auth_dir = agent_env
        controller = AgentController(folder_svc)
        run = controller.start_run(objective="Organize by category")

        assert run.state == AgentState.WAITING_FOR_APPROVAL
        plan_id = run.plan_id
        assert plan_id is not None

        # Get operation IDs from the plan
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM organization_operations WHERE plan_id = ?",
                (plan_id,),
            )
            op_ids = [row[0] for row in cursor.fetchall()]

        # Approve all operations
        for op_id in op_ids:
            ApprovalRepository.record_decision(op_id, plan_id, "APPROVED", None)

        # Agent run must still be WAITING_FOR_APPROVAL
        refreshed = controller.get_run(run.run_id)
        assert refreshed is not None
        assert refreshed.state == AgentState.WAITING_FOR_APPROVAL, (
            "Approving operations must NOT change the agent run state"
        )

    def test_reject_all_does_not_complete_agent_run(self, agent_env):
        """Rejecting all operations must not change the agent run state either."""
        folder_svc, auth_dir = agent_env
        controller = AgentController(folder_svc)
        run = controller.start_run(objective="Organize by category")

        plan_id = run.plan_id
        assert plan_id is not None

        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM organization_operations WHERE plan_id = ?",
                (plan_id,),
            )
            op_ids = [row[0] for row in cursor.fetchall()]

        for op_id in op_ids:
            ApprovalRepository.record_decision(op_id, plan_id, "REJECTED", None)

        refreshed = controller.get_run(run.run_id)
        assert refreshed is not None
        assert refreshed.state == AgentState.WAITING_FOR_APPROVAL


# ===========================================================================
# 6. Zero Filesystem Mutation (CRITICAL)
# ===========================================================================

def test_approval_zero_filesystem_mutation(tmp_path: Path):
    """CRITICAL: Prove that recording approval decisions does not touch the filesystem.

    Strategy:
    1. Create a populated directory.
    2. Take a before-snapshot (all file paths, sizes, mtimes, SHA-256 hashes).
    3. Run the full agent workflow + seed approvals + record decisions.
    4. Take an after-snapshot.
    5. Assert before == after (bit-for-bit).
    """
    db_file = tmp_path / "mutation_test.db"
    init_db(db_file)
    from app.database import connection
    orig = connection.DEFAULT_DB_PATH
    connection.DEFAULT_DB_PATH = db_file

    try:
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        (auth_dir / "document.txt").write_text("important document content", encoding="utf-8")
        (auth_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff")
        (auth_dir / "script.py").write_text("print('hello')", encoding="utf-8")

        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(auth_dir)

        # Snapshot before
        before = snapshot_directory(auth_dir)

        # Run agent to WAITING_FOR_APPROVAL
        controller = AgentController(folder_svc)
        run = controller.start_run(objective="Organize my files")
        assert run.state == AgentState.WAITING_FOR_APPROVAL
        plan_id = run.plan_id

        # Get all operations and approve/reject them alternately
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM organization_operations WHERE plan_id = ?",
                (plan_id,),
            )
            op_ids = [row[0] for row in cursor.fetchall()]

        for i, op_id in enumerate(op_ids):
            decision = "APPROVED" if i % 2 == 0 else "REJECTED"
            ApprovalRepository.record_decision(op_id, plan_id, decision, f"comment {i}")

        # Snapshot after
        after = snapshot_directory(auth_dir)

        assert before == after, (
            "CRITICAL ERROR: Filesystem was mutated during approval workflow! "
            "SmartSort Phase 11 approval must be strictly READ-ONLY."
        )
    finally:
        connection.DEFAULT_DB_PATH = orig
