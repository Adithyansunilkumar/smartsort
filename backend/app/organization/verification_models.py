from enum import Enum
from typing import Optional, List
from pydantic import BaseModel

class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"

class OperationVerificationState(BaseModel):
    operation_id: int
    plan_id: str
    status: VerificationStatus
    details: Optional[str] = None
    verified_at: str

class PlanVerificationStatus(BaseModel):
    plan_id: str
    total_operations: int
    verified_count: int
    failed_count: int
    inconclusive_count: int
    unverified_count: int
    verifications: List[OperationVerificationState]
