from typing import Optional, List, Dict, Any, Tuple
from app.database.connection import get_connection
from app.organization.execution_models import ExecutionStatus, OperationExecutionState
import datetime as dt

class ExecutionRepository:
    @staticmethod
    def get_plan_execution_status(plan_id: str) -> List[OperationExecutionState]:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT operation_id, plan_id, status, error_code, error_message, started_at, completed_at "
                "FROM operation_executions WHERE plan_id = ?",
                (plan_id,)
            )
            return [OperationExecutionState(**dict(row)) for row in cursor.fetchall()]

    @staticmethod
    def get_operation_execution(operation_id: int) -> Optional[OperationExecutionState]:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT operation_id, plan_id, status, error_code, error_message, started_at, completed_at "
                "FROM operation_executions WHERE operation_id = ?",
                (operation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return OperationExecutionState(**dict(row))

    @staticmethod
    def lock_operation_for_execution(operation_id: int) -> bool:
        """
        Atomically transition an operation from PENDING to IN_PROGRESS.
        Returns True if successful, False if it was already locked or not PENDING.
        """
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        with get_connection() as conn:
            cursor = conn.execute(
                "UPDATE operation_executions "
                "SET status = ?, started_at = ? "
                "WHERE operation_id = ? AND status = 'PENDING'",
                (ExecutionStatus.IN_PROGRESS.value, now, operation_id)
            )
            return cursor.rowcount > 0

    @staticmethod
    def mark_operation_completed(operation_id: int) -> None:
        """Mark an IN_PROGRESS operation as COMPLETED."""
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                "UPDATE operation_executions "
                "SET status = ?, completed_at = ? "
                "WHERE operation_id = ? AND status = 'IN_PROGRESS'",
                (ExecutionStatus.COMPLETED.value, now, operation_id)
            )

    @staticmethod
    def mark_operation_failed(
        operation_id: int, 
        error_code: str, 
        error_message: str,
        # Allow marking failure from PENDING (e.g. invalid before even trying) 
        # or IN_PROGRESS (failed during execution)
    ) -> None:
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                "UPDATE operation_executions "
                "SET status = ?, error_code = ?, error_message = ?, completed_at = ? "
                "WHERE operation_id = ?",
                (ExecutionStatus.FAILED.value, error_code, error_message, now, operation_id)
            )

    @staticmethod
    def mark_operation_skipped(
        operation_id: int, 
        error_code: str, 
        error_message: str
    ) -> None:
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                "UPDATE operation_executions "
                "SET status = ?, error_code = ?, error_message = ?, completed_at = ? "
                "WHERE operation_id = ? AND status = 'PENDING'",
                (ExecutionStatus.SKIPPED.value, error_code, error_message, now, operation_id)
            )
