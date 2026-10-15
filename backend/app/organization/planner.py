"""Phase 8 Organization Engine Planner.

Orchestrates deterministic planning rules and conflict detection.

All organized output is placed inside a SmartSort_Organized/ subfolder within
the authorized root.  This keeps organized files separate from originals.
Source files that already live inside SmartSort_Organized are excluded from
planning to prevent recursive processing.

Content-aware folder generation is used when extracted text or informative
filenames are available; the broad category-based fallback is used otherwise.
"""

from typing import List, Dict, Any, Set, Tuple, Optional
from pathlib import Path
import uuid
from datetime import datetime, timezone

from app.database.repositories import OrganizationRepository
from app.database.connection import get_connection
from app.organization.models import OrganizationPlan, OrganizationOperation
from app.organization.rules import (
    normalize_filename,
    get_proposed_folder_with_classification,
    generate_warnings,
)
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService

OUTPUT_FOLDER = "SmartSort_Organized"


class OrganizationPlanner:
    """Read-only deterministic planner for file organization."""

    def __init__(
        self,
        folder_service: FolderAccessService,
        protected_paths_service: ProtectedPathService,
    ):
        self.folder_service = folder_service
        self.protected_paths = protected_paths_service

    def _utcnow(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def _is_protected(self, rel_path: str) -> bool:
        """Check if a relative path intersects with any protected path."""
        return self.protected_paths.is_protected(rel_path)

    def _fetch_text_contents(
        self, file_records: List[Dict[str, Any]]
    ) -> Dict[int, str]:
        """Bulk-fetch extracted text for all files in one query."""
        file_ids = [
            r.get("file_id") or r.get("id")
            for r in file_records
            if r.get("file_id") is not None or r.get("id") is not None
        ]
        if not file_ids:
            return {}

        placeholders = ",".join("?" * len(file_ids))
        try:
            with get_connection() as conn:
                cursor = conn.execute(
                    f"SELECT file_id, text_content FROM document_content "
                    f"WHERE file_id IN ({placeholders}) AND text_content IS NOT NULL",
                    file_ids,
                )
                return {
                    row["file_id"]: row["text_content"]
                    for row in cursor.fetchall()
                    if row["text_content"]
                }
        except Exception:
            return {}

    def generate_plan(
        self,
        scan_id: str,
        files_data: List[Dict[str, Any]],
        objective: str = None,
    ) -> OrganizationPlan:
        """Generate a complete deterministic organization plan.

        All destination paths are prefixed with SmartSort_Organized/ so that
        organized output is kept in a separate folder and originals are
        preserved untouched by default.

        Files already inside SmartSort_Organized are excluded as source files
        to prevent recursive scanning and duplicate processing.

        Args:
            scan_id: The ID of the scan.
            files_data: Output from the DB join of files + analysis.
            objective: Optional user intent string.
        """
        plan_id = f"plan_{uuid.uuid4().hex}"

        operations: List[OrganizationOperation] = []
        warnings: List[str] = []
        protected_conflicts: List[str] = []

        # Track proposed destinations to detect planning-time conflicts
        proposed_destinations: Dict[str, str] = {}  # dest_path -> source_path
        # Track which output subfolders we have already queued for creation
        created_dirs: Set[str] = set()
        # Always create the top-level SmartSort_Organized folder once
        output_folder_queued = False

        # Aggregated stats for the plan summary
        detected_topics: Dict[str, int] = {}
        new_folders_proposed = 0
        existing_folders_reused = 0
        unclassified_files = 0

        if not files_data:
            warnings.append("No files were found in this scan to organize.")

        # Pre-fetch all extracted text in one query (avoids N+1)
        text_contents = self._fetch_text_contents(files_data)

        # 1. Iterate and apply rules deterministically
        for file_record in sorted(files_data, key=lambda x: x.get("relative_path", "")):
            source_rel_path = file_record.get("relative_path", "")
            filename = file_record.get("name", "")
            file_id = file_record.get("file_id") or file_record.get("id")

            # Skip files already inside SmartSort_Organized
            norm_source = source_rel_path.replace("\\", "/")
            if norm_source == OUTPUT_FOLDER or norm_source.startswith(
                OUTPUT_FOLDER + "/"
            ):
                continue

            # Security: Validate source is safely inside authorized root
            try:
                self.folder_service.validate_path_in_root(source_rel_path)
            except Exception:
                protected_conflicts.append(
                    f"Source path outside authorized root: {source_rel_path}"
                )
                continue

            # Skip if source is protected
            if self._is_protected(source_rel_path):
                protected_conflicts.append(
                    f"Cannot organize protected file: {source_rel_path}"
                )
                continue

            # Warnings (duplicates, large files, old files)
            file_warnings = generate_warnings(file_record)
            warnings.extend(file_warnings)

            # Normalization — only apply if normalization changes the name
            new_name_result = normalize_filename(filename)
            new_name = new_name_result[0] if new_name_result else filename
            rename_reason = new_name_result[1] if new_name_result else None

            # Content-aware classification
            text_content: Optional[str] = text_contents.get(file_id)  # type: ignore[arg-type]
            target_folder, classification = get_proposed_folder_with_classification(
                file_record, set(), text_content=text_content
            )

            confidence_str: str = classification.get("confidence", "LOW")
            explanation: str = classification.get("explanation", "")
            topic: str = classification.get("topic", "")

            # Track unclassified files
            if confidence_str == "LOW":
                unclassified_files += 1

            # Track detected topics
            if topic:
                detected_topics[topic] = detected_topics.get(topic, 0) + 1

            # Determine destination path — always inside SmartSort_Organized/
            if target_folder:
                dest_path = f"{OUTPUT_FOLDER}/{target_folder}/{new_name}"
                op_type = "MOVE"
                reason = explanation or f"Grouped into '{target_folder}'"
            else:
                dest_path = f"{OUTPUT_FOLDER}/{new_name}"
                op_type = "MOVE"
                reason = rename_reason or "Organized into output folder"

            # Normalize slashes
            dest_path = dest_path.replace("\\", "/")

            # Conflict Detection: Source == Destination (guard)
            if source_rel_path.replace("\\", "/") == dest_path:
                continue

            # Conflict Detection: Multiple operations targeting same destination
            if dest_path in proposed_destinations:
                other_source = proposed_destinations[dest_path]
                warnings.append(
                    f"Conflict: Both '{source_rel_path}' and '{other_source}' "
                    f"mapped to '{dest_path}'. Operation skipped."
                )
                continue

            # Conflict Detection: Destination physically exists on disk
            abs_dest = self.folder_service.require_authorized_root() / dest_path
            if abs_dest.exists():
                warnings.append(
                    f"Destination already exists on disk: {dest_path}"
                )
                continue

            # Record valid operation
            proposed_destinations[dest_path] = source_rel_path

            # Queue CREATE_FOLDER for the top-level output folder if not done yet
            if not output_folder_queued:
                output_folder_queued = True
                created_dirs.add(OUTPUT_FOLDER)

                # Check whether top-level output folder exists
                output_abs = self.folder_service.require_authorized_root() / OUTPUT_FOLDER
                output_exists = output_abs.exists()
                if output_exists:
                    existing_folders_reused += 1
                else:
                    new_folders_proposed += 1

                operations.append(
                    OrganizationOperation(
                        type="CREATE_FOLDER",
                        source=OUTPUT_FOLDER,
                        destination=OUTPUT_FOLDER,
                        reason="Create organized output folder (SmartSort_Organized)",
                        confidence="HIGH",
                        folder_exists=output_exists,
                    )
                )

            # Queue CREATE_FOLDER for each subfolder level in the hierarchy
            if target_folder:
                folder_parts = target_folder.split("/")
                cumulative = OUTPUT_FOLDER
                for part in folder_parts:
                    cumulative = f"{cumulative}/{part}"
                    if cumulative not in created_dirs:
                        created_dirs.add(cumulative)
                        subfolder_abs = (
                            self.folder_service.require_authorized_root() / cumulative
                        )
                        subfolder_exists = subfolder_abs.exists()
                        if subfolder_exists:
                            existing_folders_reused += 1
                        else:
                            new_folders_proposed += 1

                        operations.append(
                            OrganizationOperation(
                                type="CREATE_FOLDER",
                                source=cumulative,
                                destination=cumulative,
                                reason=f"Create folder '{cumulative}'",
                                confidence="HIGH",
                                folder_exists=subfolder_exists,
                            )
                        )

            operations.append(
                OrganizationOperation(
                    type=op_type,
                    source=source_rel_path,
                    destination=dest_path,
                    reason=reason,
                    confidence=confidence_str,
                    explanation=explanation,
                    topic=topic,
                )
            )

        # 2. Sort: CREATE_FOLDER ops first (so folders exist before files move in)
        operations.sort(
            key=lambda op: (0 if op.type == "CREATE_FOLDER" else 1, op.destination)
        )

        # 3. Generate summary
        summary: Dict[str, int] = {}
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
            summary=summary,
            detected_topics=detected_topics,
            new_folders_proposed=new_folders_proposed,
            existing_folders_reused=existing_folders_reused,
            unclassified_files=unclassified_files,
        )

        return plan
