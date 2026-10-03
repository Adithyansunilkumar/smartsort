"""State machine definitions for Phase 9 Agent Controller."""

from enum import Enum
from typing import Dict, Set


class AgentState(str, Enum):
    """Explicit lifecycle states for SmartSort Agent Controller."""
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal agent state transition is attempted."""

    def __init__(self, current_state: AgentState, target_state: AgentState):
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Invalid state transition: cannot transition from {current_state.value} to {target_state.value}."
        )


LEGAL_TRANSITIONS: Dict[AgentState, Set[AgentState]] = {
    AgentState.IDLE: {
        AgentState.SCANNING,
        AgentState.CANCELLED,
    },
    AgentState.SCANNING: {
        AgentState.ANALYZING,
        AgentState.ERROR,
        AgentState.CANCELLED,
    },
    AgentState.ANALYZING: {
        AgentState.PLANNING,
        AgentState.ERROR,
        AgentState.CANCELLED,
    },
    AgentState.PLANNING: {
        AgentState.WAITING_FOR_APPROVAL,
        AgentState.ERROR,
        AgentState.CANCELLED,
    },
    AgentState.WAITING_FOR_APPROVAL: {
        AgentState.COMPLETED,
        AgentState.CANCELLED,
    },
    AgentState.COMPLETED: set(),
    AgentState.ERROR: set(),
    AgentState.CANCELLED: set(),
}


def validate_transition(current_state: AgentState, target_state: AgentState) -> None:
    """Validate that transition from current_state to target_state is legal.
    
    If current_state == target_state, this is a no-op (allowed).
    Otherwise raises InvalidStateTransitionError if not permitted by LEGAL_TRANSITIONS.
    """
    if current_state == target_state:
        return
    allowed = LEGAL_TRANSITIONS.get(current_state, set())
    if target_state not in allowed:
        raise InvalidStateTransitionError(current_state, target_state)
