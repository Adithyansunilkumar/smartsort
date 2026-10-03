"""Orchestration controller for Phase 9 Agent workflow."""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional, Set

from app.agent.models import AgentRun
from app.agent.repository import AgentRepository
from app.agent.state import AgentState, validate_transition, InvalidStateTransitionError
from app.analysis.service import analyze_scan_files
from app.database.repositories import ScanRepository
from app.filesystem.scanner import run_scan
from app.organization.service import generate_organization_plan
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService

logger = logging.getLogger(__name__)

# Global in-process concurrency coordination
_run_lock = threading.Lock()
_active_run_ids: Set[str] = set()


class ActiveRunConflictError(Exception):
    """Raised when an agent run is already executing."""
    pass


class AgentController:
    """Deterministic orchestrator managing the SmartSort Agent workflow."""

    def __init__(self, folder_service: FolderAccessService):
        self.folder_service = folder_service

    def start_run(
        self,
        objective: str,
        scan_id: Optional[str] = None,
    ) -> AgentRun:
        """Initialize and execute an agent workflow run to WAITING_FOR_APPROVAL.
        
        Orchestration flow:
        1. Validate authorized root & objective
        2. Create run record (IDLE)
        3. SCANNING (fresh scan or reuse valid scan)
        4. ANALYZING (file analysis)
        5. PLANNING (organization engine plan)
        6. WAITING_FOR_APPROVAL (terminal planning state)
        """
        # 1. Validation
        cleaned_objective = objective.strip()
        if not cleaned_objective:
            raise ValueError("Objective cannot be empty.")

        self.folder_service.require_authorized_root()

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        run = AgentRun(
            run_id=run_id,
            scan_id=scan_id,
            objective=cleaned_objective,
            state=AgentState.IDLE,
            current_step="Initialized agent run session.",
            progress=0.0,
            plan_id=None,
            created_at=now_iso,
            updated_at=now_iso,
            error=None,
        )
        AgentRepository.create_run(run)

        # 2. Concurrency guard
        with _run_lock:
            if run_id in _active_run_ids:
                raise ActiveRunConflictError(f"Run '{run_id}' is already executing.")
            _active_run_ids.add(run_id)

        try:
            return self._execute_workflow(run_id, cleaned_objective, scan_id)
        finally:
            with _run_lock:
                _active_run_ids.discard(run_id)

    def _execute_workflow(
        self,
        run_id: str,
        objective: str,
        scan_id: Optional[str] = None,
    ) -> AgentRun:
        """Internal synchronous execution through workflow stages."""
        current_run = AgentRepository.get_run(run_id)
        if not current_run:
            raise ValueError(f"Run '{run_id}' not found.")

        # --- Stage 1: SCANNING ---
        if self._check_cancellation(run_id):
            res = AgentRepository.get_run(run_id)
            assert res is not None
            return res

        self._transition(run_id, AgentState.SCANNING, "Scanning authorized directory root...", 0.1)

        try:
            active_scan_id = scan_id
            if active_scan_id:
                # Verify existing scan exists in DB
                files = ScanRepository.get_files_by_scan_id(active_scan_id)
                if not files:
                    active_scan_id = None

            if not active_scan_id:
                scan_result = run_scan(self.folder_service)
                ScanRepository.save_scan(scan_result)
                active_scan_id = scan_result.scan_id

            AgentRepository.update_run_fields(run_id, scan_id=active_scan_id, progress=0.3)
        except Exception as exc:
            logger.exception("Agent run %s failed during scanning", run_id)
            return self._handle_error(run_id, f"Scanning stage failed: {self._sanitize_error(exc)}")

        # Check cancellation after scanning
        if self._check_cancellation(run_id):
            res = AgentRepository.get_run(run_id)
            assert res is not None
            return res

        # --- Stage 2: ANALYZING ---
        self._transition(run_id, AgentState.ANALYZING, "Analyzing files and calculating metadata...", 0.4)

        try:
            analyze_scan_files(active_scan_id, self.folder_service)
            AgentRepository.update_run_fields(run_id, progress=0.6)
        except Exception as exc:
            logger.exception("Agent run %s failed during analysis", run_id)
            return self._handle_error(run_id, f"Analysis stage failed: {self._sanitize_error(exc)}")

        # Check cancellation after analysis
        if self._check_cancellation(run_id):
            res = AgentRepository.get_run(run_id)
            assert res is not None
            return res

        # --- Stage 3: PLANNING ---
        self._transition(run_id, AgentState.PLANNING, "Generating organization plan proposals...", 0.7)

        try:
            plan = generate_organization_plan(
                scan_id=active_scan_id,
                folder_service=self.folder_service,
                objective=objective,
            )
            AgentRepository.update_run_fields(run_id, plan_id=plan.plan_id, progress=0.9)
        except Exception as exc:
            logger.exception("Agent run %s failed during planning", run_id)
            return self._handle_error(run_id, f"Planning stage failed: {self._sanitize_error(exc)}")

        # Check cancellation after planning
        if self._check_cancellation(run_id):
            res = AgentRepository.get_run(run_id)
            assert res is not None
            return res

        # --- Stage 4: WAITING_FOR_APPROVAL ---
        self._transition(
            run_id,
            AgentState.WAITING_FOR_APPROVAL,
            "Planning complete — waiting for your approval. No files have been modified.",
            1.0,
        )

        final_run = AgentRepository.get_run(run_id)
        assert final_run is not None
        return final_run

    def cancel_run(self, run_id: str) -> AgentRun:
        """Cancel an active or waiting agent run. Idempotent."""
        run = AgentRepository.get_run(run_id)
        if not run:
            raise ValueError(f"Run '{run_id}' not found.")

        if run.state == AgentState.CANCELLED:
            return run

        # Validate transition to CANCELLED
        validate_transition(run.state, AgentState.CANCELLED)

        updated = AgentRepository.update_run_fields(
            run_id,
            state=AgentState.CANCELLED,
            current_step="Run cancelled by user.",
            error=None,
        )
        assert updated is not None
        return updated

    def get_run(self, run_id: str) -> Optional[AgentRun]:
        """Retrieve run state."""
        return AgentRepository.get_run(run_id)

    def _transition(
        self,
        run_id: str,
        target_state: AgentState,
        step_description: str,
        progress: float,
    ) -> None:
        """Safely transition run state according to legal rules."""
        run = AgentRepository.get_run(run_id)
        if not run:
            raise ValueError(f"Run '{run_id}' not found.")

        validate_transition(run.state, target_state)
        AgentRepository.update_run_fields(
            run_id,
            state=target_state,
            current_step=step_description,
            progress=progress,
        )

    def _check_cancellation(self, run_id: str) -> bool:
        """Check whether the run has been cancelled in the database."""
        run = AgentRepository.get_run(run_id)
        return run is not None and run.state == AgentState.CANCELLED

    def _handle_error(self, run_id: str, safe_message: str) -> AgentRun:
        """Transition run to ERROR state with a sanitized error message."""
        run = AgentRepository.get_run(run_id)
        if run and run.state != AgentState.ERROR:
            try:
                validate_transition(run.state, AgentState.ERROR)
            except InvalidStateTransitionError:
                pass
            AgentRepository.update_run_fields(
                run_id,
                state=AgentState.ERROR,
                current_step="Encountered an error during execution.",
                error=safe_message,
            )
        err_run = AgentRepository.get_run(run_id)
        assert err_run is not None
        return err_run

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Produce a safe, client-friendly error message without leaking internal paths or secrets."""
        if isinstance(exc, PathSecurityError):
            return f"Security boundary violation: {exc}"
        msg = str(exc)
        if len(msg) > 200:
            msg = msg[:197] + "..."
        return msg if msg else "An unexpected internal error occurred."
