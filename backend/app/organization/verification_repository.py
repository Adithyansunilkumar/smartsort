import datetime as dt
from typing import Optional, List
from app.database.connection import get_connection
from app.organization.verification_models import VerificationStatus, OperationVerificationState


class VerificationRepository:
    @staticmethod
    def get_plan_verification_status(plan_id: str) -> List[OperationVerificationState]:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT operation_id, plan_id, status, details, verified_at "
                "FROM operation_verifications WHERE plan_id = ?",
                (plan_id,)
            )
            return [OperationVerificationState(**dict(row)) for row in cursor.fetchall()]

    @staticmethod
    def get_operation_verification(operation_id: int) -> Optional[OperationVerificationState]:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT operation_id, plan_id, status, details, verified_at "
                "FROM operation_verifications WHERE operation_id = ?",
                (operation_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return OperationVerificationState(**dict(row))

    @staticmethod
    def save_verification(
        operation_id: int,
        plan_id: str,
        status: VerificationStatus,
        details: Optional[str] = None
    ) -> OperationVerificationState:
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        with get_connection() as conn:
            # Append an immutable history row for auditability (mirrors
            # Phase 11 operation_approval_history convention).
            conn.execute(
                """
                INSERT INTO operation_verification_history
                    (operation_id, plan_id, status, details, verified_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (operation_id, plan_id, status.value, details, now)
            )

            # Upsert the current-state row for efficient lookups.
            conn.execute(
                """
                INSERT INTO operation_verifications
                    (operation_id, plan_id, status, details, verified_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    status=excluded.status,
                    details=excluded.details,
                    verified_at=excluded.verified_at
                """,
                (operation_id, plan_id, status.value, details, now)
            )

            cursor = conn.execute(
                "SELECT operation_id, plan_id, status, details, verified_at "
                "FROM operation_verifications WHERE operation_id = ?",
                (operation_id,)
            )
            row = cursor.fetchone()
            return OperationVerificationState(**dict(row))
