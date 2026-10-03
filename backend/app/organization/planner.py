"""Phase 8 Organization Engine Planner.

Orchestrates deterministic planning rules and conflict detection.
"""

from typing import List, Dict, Any, Set, Tuple
from pathlib import Path
import uuid
from datetime import datetime, timezone

from app.database.repositories import OrganizationRepository
from app.organization.models import OrganizationPlan, OrganizationOperation
from app.organization.rules import normalize_filename, get_proposed_folder, generate_warnings
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService


class OrganizationPlanner:
    """Read-only deterministic planner for file organization."""

    def __init__(
        self,
        folder_service: FolderAccessService,
        protected_paths_service: ProtectedPathService
    ):
        self.folder_service = folder_service
        self.protected_paths = protected_paths_service
        
    def _utcnow(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()
        
    def _is_protected(self, rel_path: str) -> bool:
        """Check if a relative path intersects with any protected path rules."""
        return self.protected_paths.is_protected(rel_path)

    def generate_plan(
        self,
        scan_id: str,
        files_data: List[Dict[str, Any]],
        objective: str = None
    ) -> OrganizationPlan:
        """Generate a complete deterministic organization plan.

        Args:
            scan_id: The ID of the scan.
            files_data: Output from `AnalysisRepository.get_analysis_results_for_scan`.
            objective: Optional user intent string.
        """
        plan_id = f"plan_{uuid.uuid4().hex}"
        
        # 1. Discover existing directories to prefer them over creating new ones.
        existing_dirs: Set[str] = set()
        root_path = self.folder_service.require_authorized_root()
        for f in files_data:
            rel_path = f.get("relative_path", "")
            parent = str(Path(rel_path).parent)
            if parent and parent != ".":
                # Only storing root-level dirs for now as our grouping targets root
                root_level_dir = parent.replace("\\", "/").split("/")[0]
                # Validate it actually exists on disk before considering it existing!
                if (root_path / root_level_dir).is_dir():
                    existing_dirs.add(root_level_dir)

        operations: List[OrganizationOperation] = []
        warnings: List[str] = []
        protected_conflicts: List[str] = []
        
        # Track proposed destinations to detect planning-time conflicts
        proposed_destinations: Dict[str, str] = {} # dest_path -> source_path
        created_dirs: Set[str] = set()

        # 2. Iterate and apply rules deterministically
        for file_record in sorted(files_data, key=lambda x: x["relative_path"]):
            source_rel_path = file_record["relative_path"]
            filename = file_record["name"]

            # Security: Validate source is safely inside authorized root
            try:
                self.folder_service.validate_path_in_root(source_rel_path)
            except Exception as e:
                protected_conflicts.append(f"Source path outside authorized root: {source_rel_path}")
                continue
            
            # Skip if source is protected
            if self._is_protected(source_rel_path):
                protected_conflicts.append(f"Cannot organize protected file: {source_rel_path}")
                continue

            # Warnings (Large, Old, Duplicates)
            file_warnings = generate_warnings(file_record)
            warnings.extend(file_warnings)
            
            # Normalization
            new_name, rename_reason = normalize_filename(filename) or (filename, None)
            
            # Grouping
            target_folder = get_proposed_folder(file_record, existing_dirs)
            
            # If no rename and no move, do nothing
            if new_name == filename and not target_folder:
                continue
                
            # Determine destination path
            if target_folder:
                dest_path = f"{target_folder}/{new_name}"
                op_type = "MOVE"
                reason = f"Grouped by category ({file_record.get('category')})"
            else:
                current_parent = str(Path(source_rel_path).parent)
                if current_parent == ".":
                    dest_path = new_name
                else:
                    dest_path = f"{current_parent}/{new_name}".replace("\\", "/")
                op_type = "RENAME"
                reason = rename_reason
                
            # Clean up slashes
            dest_path = dest_path.replace("\\", "/")

            # Security: Validate destination is safely inside authorized root
            try:
                # If we are explicitly creating this folder, we validate the root itself
                # or we validate with allow_nonexistent=True, but wait, validate_path_in_root
                # checks if the parent exists. If we are creating the parent, we should check
                # if the parent's parent exists. But for root-level folders, the parent's parent
                # is the root, which exists.
                # To be safe, we just validate the dest_path, but if it fails because of missing parent,
                # and we are creating that parent, we allow it.
                from app.security.paths import PathSecurityError
                try:
                    self.folder_service.validate_path_in_root(dest_path, allow_nonexistent=True)
                except PathSecurityError as exc:
                    if exc.code == "PATH_OUTSIDE_ROOT" and "parent path does not exist" in exc.message:
                        parent_path = str(Path(dest_path).parent).replace("\\", "/")
                        if parent_path == ".":
                            parent_path = ""
                        
                        # Is this a folder we are planning to create?
                        if target_folder and parent_path == target_folder:
                            # It's fine, we will create it. We still need to make sure target_folder itself is valid.
                            self.folder_service.validate_path_in_root(target_folder, allow_nonexistent=True)
                        else:
                            raise
                    else:
                        raise
            except Exception as e:
                protected_conflicts.append(f"Proposed destination outside authorized root: {dest_path} ({e})")
                continue
                
            # Security: Check destination protection
            if self._is_protected(dest_path):
                protected_conflicts.append(f"Proposed destination is protected: {dest_path}")
                continue

            # Conflict Detection: Source == Destination
            if source_rel_path == dest_path:
                continue
                
            # Conflict Detection: Multiple operations targeting same destination
            if dest_path in proposed_destinations:
                other_source = proposed_destinations[dest_path]
                warnings.append(f"Conflict: Both '{source_rel_path}' and '{other_source}' mapped to '{dest_path}'. Operation skipped.")
                continue
                
            # Conflict Detection: Destination already exists in actual scan files
            if any(f["relative_path"].replace("\\", "/") == dest_path for f in files_data):
                warnings.append(f"Conflict: Destination '{dest_path}' already exists. Operation skipped.")
                continue

            # Add valid operation
            proposed_destinations[dest_path] = source_rel_path
            
            # Add CREATE_FOLDER dependency if needed
            if target_folder and target_folder not in existing_dirs and target_folder not in created_dirs:
                created_dirs.add(target_folder)
                operations.append(
                    OrganizationOperation(
                        type="CREATE_FOLDER",
                        source=target_folder,
                        destination=target_folder,
                        reason=f"Create category folder ({file_record.get('category')})",
                        confidence="HIGH"
                    )
                )
            
            operations.append(
                OrganizationOperation(
                    type=op_type,
                    source=source_rel_path,
                    destination=dest_path,
                    reason=reason,
                    confidence="HIGH"
                )
            )

        # 3. Sort operations deterministically
        operations.sort(key=lambda op: (op.type, op.source, op.destination))
        
        # 4. Generate summary
        summary = {}
        for op in operations:
            summary[op.type] = summary.get(op.type, 0) + 1

        plan = OrganizationPlan(
            plan_id=plan_id,
            scan_id=scan_id,
            objective=objective,
            created_at=self._utcnow(),
            operations=operations,
            warnings=list(set(warnings)),
            protected_conflicts=list(set(protected_conflicts)),
            summary=summary
        )

        return plan
