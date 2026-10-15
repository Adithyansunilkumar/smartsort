"""Phase 8 Organization Service.

Coordinates the API boundary, database repository, and deterministic planner.
"""

from typing import Optional
from app.database.repositories import OrganizationRepository, ScanRepository
from app.database.connection import get_connection
from app.organization.models import OrganizationPlan
from app.organization.planner import OrganizationPlanner
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService


def generate_organization_plan(
    scan_id: str,
    folder_service: FolderAccessService,
    objective: Optional[str] = None,
    structure: Optional[str] = None,
    rename: bool = False,
    archive_days: Optional[int] = None,
) -> OrganizationPlan:
    """Generate and persist an organization plan for a scan."""

    # 1. Verify scan exists
    with get_connection() as conn:
        cursor = conn.execute("SELECT 1 FROM scans WHERE id = ?", (scan_id,))
        if not cursor.fetchone():
            raise ValueError(f"Scan ID '{scan_id}' not found.")

    # 2. Fetch required data (Files + Analysis joined)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT f.*, a.*,
                   f.path as relative_path,
                   f.name as name
            FROM files f
            LEFT JOIN file_analysis a ON f.id = a.file_id
            WHERE f.scan_id = ?
            """,
            (scan_id,)
        )
        files_data = [dict(row) for row in cursor.fetchall()]

    # If scan has files but analysis was not yet run, attempt analysis first
    if files_data and any(f.get("category") is None for f in files_data):
        try:
            from app.analysis.service import analyze_scan_files
            analyze_scan_files(scan_id, folder_service)
            with get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT f.*, a.*,
                           f.path as relative_path,
                           f.name as name
                    FROM files f
                    LEFT JOIN file_analysis a ON f.id = a.file_id
                    WHERE f.scan_id = ?
                    """,
                    (scan_id,)
                )
                files_data = [dict(row) for row in cursor.fetchall()]
        except Exception:
            pass

    # 3. Setup services
    protected_service = ProtectedPathService(folder_service)
    planner = OrganizationPlanner(
        folder_service=folder_service,
        protected_paths_service=protected_service
    )

    # 4. Generate deterministic plan
    plan = planner.generate_plan(
        scan_id=scan_id,
        files_data=files_data,
        objective=objective,
        structure=structure,
        rename=rename,
        archive_days=archive_days,
    )

    # 5. Persist the plan
    op_dicts = [op.model_dump() for op in plan.operations]
    OrganizationRepository.save_plan(
        plan_id=plan.plan_id,
        scan_id=plan.scan_id,
        objective=plan.objective,
        created_at=plan.created_at,
        warnings=plan.warnings,
        protected_conflicts=plan.protected_conflicts,
        operations=op_dicts,
        detected_topics=plan.detected_topics,
        new_folders_proposed=plan.new_folders_proposed,
        existing_folders_reused=plan.existing_folders_reused,
        unclassified_files=plan.unclassified_files,
        structure=plan.structure,
        rename=plan.rename,
        archive_days=plan.archive_days,
    )

    return plan


def retrieve_organization_plan(plan_id: str) -> Optional[OrganizationPlan]:
    """Retrieve an existing persisted plan by ID."""
    data = OrganizationRepository.get_plan(plan_id)
    if not data:
        return None
        
    return OrganizationPlan(**data)
