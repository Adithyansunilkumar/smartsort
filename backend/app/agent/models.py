"""Pydantic models for Phase 9 Agent Controller."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator
from app.agent.state import AgentState


class AgentRun(BaseModel):
    """Represents the complete state of an Agent Controller run session."""
    run_id: str = Field(..., description="Unique run identifier")
    scan_id: Optional[str] = Field(None, description="Linked scan ID")
    objective: str = Field(..., description="User organization directive")
    state: AgentState = Field(..., description="Current agent state")
    current_step: Optional[str] = Field(None, description="Human-readable current step")
    progress: float = Field(0.0, description="Workflow progress from 0.0 to 1.0")
    plan_id: Optional[str] = Field(None, description="Linked organization plan ID")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    updated_at: str = Field(..., description="ISO 8601 last update timestamp")
    error: Optional[str] = Field(None, description="Safe error message if state is ERROR")


class AgentRunCreateRequest(BaseModel):
    """Payload to start a new agent workflow run."""
    objective: str = Field(..., description="User organization objective (must be non-empty)")
    scan_id: Optional[str] = Field(None, description="Optional existing scan ID to reuse")

    @field_validator("objective")
    @classmethod
    def validate_objective_not_empty(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Objective cannot be empty or whitespace only.")
        return trimmed


class AgentRunResponse(BaseModel):
    """API response wrapping an AgentRun."""
    run: AgentRun
