"""Orchestration, mutation safety, and cancellation tests for Agent Controller (Phase 9)."""

import hashlib
import os
from pathlib import Path
import pytest

from app.agent.controller import AgentController
from app.agent.models import AgentRun
from app.agent.repository import AgentRepository
from app.agent.state import AgentState
from app.database.schema import init_db
from app.services.folder_access import FolderAccessService


def snapshot_directory(root: Path) -> dict:
    """Take a deep snapshot of all files and directories in root."""
    snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        snapshot[rel_dir] = {
            "is_dir": True,
            "subdirs": sorted(dirnames),
            "files": sorted(filenames),
        }
        for f in filenames:
            file_path = Path(dirpath) / f
            stat = file_path.stat()
            content = file_path.read_bytes()
            hasher = hashlib.sha256(content).hexdigest()
            rel_file = os.path.relpath(file_path, root)
            snapshot[rel_file] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime_ns,
                "hash": hasher,
            }
    return snapshot


@pytest.fixture
def setup_env(tmp_path):
    """Set up database and authorized root folder with sample files."""
    db_file = tmp_path / "test_agent_controller.db"
    init_db(db_file)
    from app.database import connection
    orig_path = connection.DEFAULT_DB_PATH
    connection.DEFAULT_DB_PATH = db_file

    auth_dir = tmp_path / "authorized_root"
    auth_dir.mkdir(parents=True, exist_ok=True)

    # Create diverse sample files
    (auth_dir / "report.txt").write_text("Q3 Financial Analysis and Summary Report", encoding="utf-8")
    (auth_dir / "script.py").write_text("import sys\nprint('hello world')", encoding="utf-8")
    (auth_dir / "notes.txt").write_text("Meeting notes for engineering alignment", encoding="utf-8")

    sub_dir = auth_dir / "misc"
    sub_dir.mkdir()
    (sub_dir / "data.json").write_text('{"key": "value"}', encoding="utf-8")

    folder_service = FolderAccessService()
    folder_service.set_authorized_root(auth_dir)

    yield folder_service, auth_dir

    connection.DEFAULT_DB_PATH = orig_path


class TestAgentControllerOrchestration:
    """Test full agent orchestration flow."""

    def test_full_orchestration_success(self, setup_env):
        folder_service, auth_dir = setup_env
        controller = AgentController(folder_service)

        run = controller.start_run(objective="Organize documents by category")

        assert run is not None
        assert run.state == AgentState.WAITING_FOR_APPROVAL
        assert run.scan_id is not None
        assert run.plan_id is not None
        assert run.progress == 1.0
        assert run.error is None
        assert "waiting for your approval" in run.current_step.lower()

        # Verify plan was persisted and has operations
        from app.organization.service import retrieve_organization_plan
        plan = retrieve_organization_plan(run.plan_id)
        assert plan is not None
        assert plan.scan_id == run.scan_id
        assert len(plan.operations) > 0

    def test_empty_objective_rejected(self, setup_env):
        folder_service, auth_dir = setup_env
        controller = AgentController(folder_service)

        with pytest.raises(ValueError, match="Objective cannot be empty"):
            controller.start_run(objective="   ")

    def test_cancellation_workflow(self, setup_env):
        folder_service, auth_dir = setup_env
        controller = AgentController(folder_service)

        # Run to WAITING_FOR_APPROVAL
        run = controller.start_run(objective="Organize files")
        assert run.state == AgentState.WAITING_FOR_APPROVAL

        # Cancel it
        cancelled_run = controller.cancel_run(run.run_id)
        assert cancelled_run.state == AgentState.CANCELLED
        assert "cancelled" in cancelled_run.current_step.lower()

        # Cancel again is idempotent
        re_cancelled = controller.cancel_run(run.run_id)
        assert re_cancelled.state == AgentState.CANCELLED


def test_mutation_safety(setup_env):
    """CRITICAL REGRESSION TEST: Verify 100% zero filesystem mutation during entire agent workflow."""
    folder_service, auth_dir = setup_env
    controller = AgentController(folder_service)

    # 1. Snapshot directory before running agent controller
    before_snapshot = snapshot_directory(auth_dir)

    # 2. Execute the complete agent workflow: scan → analyze → plan → WAITING_FOR_APPROVAL
    run = controller.start_run(objective="Categorize all items cleanly")
    assert run.state == AgentState.WAITING_FOR_APPROVAL

    # 3. Snapshot directory after running agent controller
    after_snapshot = snapshot_directory(auth_dir)

    # 4. Strict assertion: before and after must be bit-for-bit identical
    assert before_snapshot == after_snapshot, (
        "CRITICAL ERROR: Filesystem was mutated during Agent Controller workflow! "
        "SmartSort Phase 9 must be strictly READ-ONLY until approved."
    )
