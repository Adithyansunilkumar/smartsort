from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class ExecutionStatus(str, Enum):
    PENDING = "PENDING"          # Not attempted yet
    IN_PROGRESS = "IN_PROGRESS"  # Currently running
    COMPLETED = "COMPLETED"      # Successfully executed
    FAILED = "FAILED"            # Attempted but encountered an error
    SKIPPED = "SKIPPED"          # Skipped because unapproved, ineligible, or missing

class OperationExecutionState(BaseModel):
    operation_id: int
    plan_id: str
    status: ExecutionStatus
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

class PlanExecutionStatus(BaseModel):
    plan_id: str
    total_operations: int
    completed_count: int
    failed_count: int
    skipped_count: int
    in_progress_count: int
    pending_count: int
    executions: List[OperationExecutionState]

class ExecutePlanRequest(BaseModel):
    # Optional list of operation IDs to execute. If empty, executes all APPROVED pending operations.
    operation_ids: Optional[List[int]] = None
