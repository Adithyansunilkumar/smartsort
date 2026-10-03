"""Unit tests for Agent Controller state machine and repository (Phase 9)."""

import pytest
from app.agent.state import (
    AgentState,
    InvalidStateTransitionError,
    validate_transition,
    LEGAL_TRANSITIONS,
)
from app.agent.models import AgentRun
from app.agent.repository import AgentRepository
from app.database.schema import init_db


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_agent_state.db"
    init_db(db_file)
    from app.database import connection
    orig_path = connection.DEFAULT_DB_PATH
    connection.DEFAULT_DB_PATH = db_file
    yield
    connection.DEFAULT_DB_PATH = orig_path


class TestAgentStateMachine:
    """Test state machine legal and illegal transitions."""

    def test_valid_forward_transitions(self):
        """Test the standard forward orchestration path."""
        validate_transition(AgentState.IDLE, AgentState.SCANNING)
        validate_transition(AgentState.SCANNING, AgentState.ANALYZING)
        validate_transition(AgentState.ANALYZING, AgentState.PLANNING)
        validate_transition(AgentState.PLANNING, AgentState.WAITING_FOR_APPROVAL)

    def test_valid_same_state_noop(self):
        """Transitioning to the same state is a legal no-op."""
        for state in AgentState:
            validate_transition(state, state)

    def test_valid_error_transitions(self):
        """Error can be reached from intermediate states."""
        validate_transition(AgentState.SCANNING, AgentState.ERROR)
        validate_transition(AgentState.ANALYZING, AgentState.ERROR)
        validate_transition(AgentState.PLANNING, AgentState.ERROR)

    def test_valid_cancellation_transitions(self):
        """Cancellation can be triggered from intermediate and waiting states."""
        validate_transition(AgentState.IDLE, AgentState.CANCELLED)
        validate_transition(AgentState.SCANNING, AgentState.CANCELLED)
        validate_transition(AgentState.ANALYZING, AgentState.CANCELLED)
        validate_transition(AgentState.PLANNING, AgentState.CANCELLED)
        validate_transition(AgentState.WAITING_FOR_APPROVAL, AgentState.CANCELLED)

    def test_invalid_skip_transitions(self):
        """Skipping intermediate stages must raise InvalidStateTransitionError."""
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.IDLE, AgentState.PLANNING)

        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.IDLE, AgentState.WAITING_FOR_APPROVAL)

        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.SCANNING, AgentState.WAITING_FOR_APPROVAL)

    def test_invalid_backward_transitions(self):
        """Backward transitions must raise InvalidStateTransitionError."""
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.WAITING_FOR_APPROVAL, AgentState.SCANNING)

        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.PLANNING, AgentState.SCANNING)

        with pytest.raises(InvalidStateTransitionError):
            validate_transition(AgentState.ANALYZING, AgentState.SCANNING)

    def test_invalid_terminal_transitions(self):
        """Cannot transition out of terminal states (ERROR, CANCELLED, COMPLETED)."""
        terminal_states = [AgentState.ERROR, AgentState.CANCELLED, AgentState.COMPLETED]
        for term in terminal_states:
            for target in AgentState:
                if target != term:
                    with pytest.raises(InvalidStateTransitionError):
                        validate_transition(term, target)


class TestAgentRepository:
    """Test persistence and retrieval of agent runs."""

    def test_create_and_get_run(self):
        run = AgentRun(
            run_id="run_test_001",
            scan_id=None,
            objective="Organize research papers",
            state=AgentState.IDLE,
            current_step="Ready",
            progress=0.0,
            plan_id=None,
            created_at="2026-09-19T22:00:00Z",
            updated_at="2026-09-19T22:00:00Z",
            error=None,
        )
        AgentRepository.create_run(run)

        fetched = AgentRepository.get_run("run_test_001")
        assert fetched is not None
        assert fetched.run_id == "run_test_001"
        assert fetched.objective == "Organize research papers"
        assert fetched.state == AgentState.IDLE
        assert fetched.progress == 0.0

    def test_update_run_fields(self):
        from app.database.connection import get_connection
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO scans (id, root_path, started_at, status) VALUES (?, ?, ?, ?)",
                ("scan_123", "C:/test", "2026-09-19T22:00:00Z", "completed"),
            )

        run = AgentRun(
            run_id="run_test_002",
            scan_id=None,
            objective="Clean up downloads",
            state=AgentState.IDLE,
            current_step="Starting",
            progress=0.0,
            plan_id=None,
            created_at="2026-09-19T22:00:00Z",
            updated_at="2026-09-19T22:00:00Z",
            error=None,
        )
        AgentRepository.create_run(run)

        updated = AgentRepository.update_run_fields(
            "run_test_002",
            state=AgentState.SCANNING,
            current_step="Scanning filesystem",
            progress=0.25,
            scan_id="scan_123",
        )
        assert updated is not None
        assert updated.state == AgentState.SCANNING
        assert updated.progress == 0.25
        assert updated.scan_id == "scan_123"

    def test_get_nonexistent_run(self):
        assert AgentRepository.get_run("run_does_not_exist") is None
