"""Phase 8 Organization Engine Planner.

Orchestrates deterministic planning rules and conflict detection.

All organized output is placed inside a SmartSort_Organized/ subfolder within
the authorized root.  This keeps organized files separate from originals.
Source files that already live inside SmartSort_Organized are excluded from
planning to prevent recursive processing.

Human-thinking rules implemented
---------------------------------
A. Category: name hints > MIME mismatch > extension > semantic > content > _Review
B. Destination: auto structure, synonym reuse, min-3 threshold, companion units,
   dev-project units, max-100 split, reserved folders (_Review, _Duplicates, _Empty)
C. Naming: rename off by default; (2) suffix for same-name/different-content
D. Duplicates: clearest-name winner, others → _Duplicates/
E. Conflicts: (2) suffix instead of _1; likely_newer flag; never overwrite
F. Metadata: rule, confidence_score, flags, date_source, original_name,
   duplicate_of, likely_newer, needs_review on every operation
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
from datetime import datetime, timezone

from app.database.repositories import OrganizationRepository
from app.database.connection import get_connection
from app.organization.models import OrganizationPlan, OrganizationOperation
from app.organization.classifier import classify_file
from app.organization.rules import (
    MAX_DEPTH,
    MIN_FILES_PER_FOLDER,
    MAX_ITEMS_PER_FOLDER,
    PARTIAL_DOWNLOAD_EXTS,
    SIDECAR_EXTS,
    DEV_PROJECT_MARKERS,
    DEV_PROJECT_GLOB_MARKERS,
    generate_warnings,
    normalize_filename,
    match_existing_folder,
    score_name_clarity,
    detect_version_variant,
    make_dedup_suffix,
    tokenize_for_project,
    detect_filename_date,
    is_partial_download,
    is_sidecar,
)
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService

OUTPUT_FOLDER = "SmartSort_Organized"

# ---------------------------------------------------------------------------
# Auto structure detection thresholds
# ---------------------------------------------------------------------------
PHOTO_VIDEO_THRESHOLD = 0.60   # > 60% images/videos → date axis
PROJECT_TOKEN_THRESHOLD = 3    # token in 3+ files → project folder
LARGE_SCAN_THRESHOLD = 500     # > 500 files → type_year axis


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
        return self.protected_paths.is_protected(rel_path)

    def _authorized_root(self) -> Path:
        return self.folder_service.require_authorized_root()

    def _fetch_text_contents(self, file_records: List[Dict[str, Any]]) -> Dict[int, str]:
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

    # ------------------------------------------------------------------ #
    # Structure auto-detection                                            #
    # ------------------------------------------------------------------ #
    def _detect_structure(
        self,
        files_data: List[Dict[str, Any]],
        structure_option: Optional[str],
    ) -> str:
        """Return the effective structure axis to use."""
        if structure_option and structure_option != "auto":
            return structure_option
        if not files_data:
            return "type"
        total = len(files_data)
        media_count = sum(
            1 for f in files_data
            if (f.get("category") or "").lower() in ("image", "video")
        )
        if total > LARGE_SCAN_THRESHOLD:
            return "type_year"
        if media_count / total > PHOTO_VIDEO_THRESHOLD:
            return "date"
        # Project detection: any token appearing in ≥ 3 file names
        token_counts: Counter = Counter()
        for f in files_data:
            for t in tokenize_for_project(f.get("name", "")):
                token_counts[t] += 1
        if any(c >= PROJECT_TOKEN_THRESHOLD for c in token_counts.values()):
            return "project"
        return "type"

    # ------------------------------------------------------------------ #
    # Sidecar companion grouping                                         #
    # ------------------------------------------------------------------ #
    def _build_sidecar_map(
        self, file_records: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        """Return {sidecar_rel_path → parent_rel_path}."""
        # Build stem → first non-sidecar match map
        stem_to_parent: Dict[str, str] = {}
        for rec in file_records:
            name = rec.get("name", "")
            rel = rec.get("relative_path", "")
            ext = Path(name).suffix.lower()
            if ext not in SIDECAR_EXTS:
                stem = re.sub(r"\.[^.]+$", "", name)
                key = str(Path(rel).parent / stem)
                stem_to_parent[key] = rel

        sidecar_map: Dict[str, str] = {}
        for rec in file_records:
            name = rec.get("name", "")
            rel = rec.get("relative_path", "")
            ext = Path(name).suffix.lower()
            if ext in SIDECAR_EXTS:
                stem = re.sub(r"\.[^.]+$", "", name)
                key = str(Path(rel).parent / stem)
                parent = stem_to_parent.get(key)
                if parent:
                    sidecar_map[rel] = parent
        return sidecar_map

    # ------------------------------------------------------------------ #
    # Dev-project folder detection                                        #
    # ------------------------------------------------------------------ #
    def _find_dev_project_folders(
        self, file_records: List[Dict[str, Any]]
    ) -> Set[str]:
        """Return set of root-relative folder paths that are dev projects."""
        root = self._authorized_root()
        candidate_dirs: Set[str] = set()
        for rec in file_records:
            rel = rec.get("relative_path", "").replace("\\", "/")
            parts = rel.split("/")
            if len(parts) >= 2:
                candidate_dirs.add(parts[0])  # top-level dirs only
        project_dirs: Set[str] = set()
        for d in candidate_dirs:
            abs_dir = root / d
            is_project = False
            if abs_dir.is_dir():
                for marker in DEV_PROJECT_MARKERS:
                    if (abs_dir / marker).exists():
                        is_project = True
                        break
                if not is_project:
                    for glob_pat in DEV_PROJECT_GLOB_MARKERS:
                        if any(abs_dir.glob(glob_pat)):
                            is_project = True
                            break
            # Also check in-memory records
            if not is_project:
                for rec in file_records:
                    rel = rec.get("relative_path", "").replace("\\", "/")
                    if rel.startswith(f"{d}/"):
                        sub_name = rec.get("name", "")
                        sub_rel = rel[len(d)+1:]
                        if sub_name in DEV_PROJECT_MARKERS or sub_rel in DEV_PROJECT_MARKERS or sub_name == ".git":
                            is_project = True
                            break
                        for glob_pat in DEV_PROJECT_GLOB_MARKERS:
                            ext = glob_pat.replace("*", "")
                            if sub_name.endswith(ext):
                                is_project = True
                                break
                    if is_project:
                        break
            if is_project:
                project_dirs.add(d)
        return project_dirs

    # ------------------------------------------------------------------ #
    # Duplicate detection (byte-identical: same hash)                    #
    # ------------------------------------------------------------------ #
    def _build_duplicate_groups(
        self, file_records: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return {hash → [records]} for groups with > 1 member."""
        hash_groups: Dict[str, List[Dict[str, Any]]] = {}
        for rec in file_records:
            h = rec.get("sha256") or rec.get("hash")
            if not h:
                continue
            hash_groups.setdefault(h, []).append(rec)
        return {h: recs for h, recs in hash_groups.items() if len(recs) > 1}

    def _pick_duplicate_winner(self, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pick the best copy to keep, per spec priority:
        1. Not in Downloads/temp folder
        2. Clearest name (score_name_clarity)
        3. Oldest modified_time
        4. Shortest path
        """
        bad_dirs = {"downloads", "temp", "tmp", "desktop", "trash"}
        def sort_key(r: Dict[str, Any]) -> tuple:
            rel = r.get("relative_path", "").lower().replace("\\", "/")
            in_bad = int(any(d in rel for d in bad_dirs))
            clarity = -score_name_clarity(r.get("name", ""))
            mtime = r.get("modified_time") or r.get("modified_at") or ""
            path_len = len(rel)
            return (in_bad, clarity, mtime, path_len)
        return sorted(recs, key=sort_key)[0]

    # ------------------------------------------------------------------ #
    # Folder-count tracking (for max-100 split)                          #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Main plan generation                                                #
    # ------------------------------------------------------------------ #
    def generate_plan(
        self,
        scan_id: str,
        files_data: List[Dict[str, Any]],
        objective: Optional[str] = None,
        structure: Optional[str] = None,
        rename: bool = False,
        archive_days: Optional[int] = None,
    ) -> OrganizationPlan:
        """Generate a complete deterministic organization plan.

        All destination paths are prefixed with SmartSort_Organized/ so that
        organized output is kept in a separate folder and originals are
        preserved untouched by default.
        """
        plan_id = f"plan_{uuid.uuid4().hex}"
        root = self._authorized_root()

        operations: List[OrganizationOperation] = []
        warnings: List[str] = []
        protected_conflicts: List[str] = []

        # Destination occupancy table (planned + existing on disk)
        # Maps dest_path → source_path
        planned_dests: Dict[str, str] = {}
        created_dirs: Set[str] = set()
        output_folder_queued = False

        detected_topics: Dict[str, int] = {}
        new_folders_proposed = 0
        existing_folders_reused = 0
        unclassified_files = 0

        # Per-folder item count tracking (for max-100 splitting)
        folder_item_counts: Dict[str, int] = Counter()

        # Existing directories in SmartSort_Organized for synonym / reuse matching
        existing_org_dirs: Set[str] = set()
        org_root = root / OUTPUT_FOLDER
        if org_root.is_dir():
            try:
                existing_org_dirs = {p.name for p in org_root.iterdir() if p.is_dir()}
            except Exception:
                pass

        if not files_data:
            warnings.append("No files were found in this scan to organize.")

        # ── Pre-flight: detect structure axis ──────────────────────────
        effective_structure = self._detect_structure(files_data, structure)

        # ── Pre-flight: sidecar map ─────────────────────────────────────
        sidecar_map = self._build_sidecar_map(files_data)

        # ── Pre-flight: dev-project dirs ────────────────────────────────
        project_dirs = self._find_dev_project_folders(files_data)

        # ── Pre-flight: duplicate groups ────────────────────────────────
        dup_groups = self._build_duplicate_groups(files_data)
        # Which relative paths are duplicate losers (not the winner)
        dup_loser_paths: Dict[str, Dict[str, Any]] = {}  # rel_path → group info
        for h, recs in dup_groups.items():
            winner = self._pick_duplicate_winner(recs)
            for rec in recs:
                if rec is not winner:
                    dup_loser_paths[rec.get("relative_path", "").replace("\\", "/")] = {
                        "hash": h,
                        "winner_path": winner.get("relative_path", "").replace("\\", "/"),
                        "winner_name": winner.get("name", ""),
                    }

        # ── Pre-flight: text contents ───────────────────────────────────
        text_contents = self._fetch_text_contents(files_data)

        # ── Main loop ───────────────────────────────────────────────────
        for file_record in sorted(files_data, key=lambda x: x.get("relative_path", "")):
            source_rel = file_record.get("relative_path", "")
            filename = file_record.get("name", "")
            file_id = file_record.get("file_id") or file_record.get("id")

            # Skip files already inside SmartSort_Organized
            norm_source = source_rel.replace("\\", "/")
            if norm_source == OUTPUT_FOLDER or norm_source.startswith(OUTPUT_FOLDER + "/"):
                continue

            # Security: validate source inside authorized root
            try:
                self.folder_service.validate_path_in_root(source_rel)
            except Exception:
                protected_conflicts.append(f"Source path outside authorized root: {source_rel}")
                continue

            if self._is_protected(source_rel):
                protected_conflicts.append(f"Cannot organize protected file: {source_rel}")
                continue

            # ── Skip partial downloads ─────────────────────────────────
            if is_partial_download(filename):
                warnings.append(f"Skipped partial download: {filename}")
                continue

            # ── Warnings ───────────────────────────────────────────────
            file_warnings = generate_warnings(file_record)
            warnings.extend(file_warnings)

            # ── Dev-project: keep folder as unit ──────────────────────
            parts = norm_source.split("/")
            top_dir = parts[0] if len(parts) >= 2 else None
            if top_dir and top_dir in project_dirs:
                target_folder = "Development/Projects"
                sub_rel = "/".join(parts[1:])
                dest_path = f"{OUTPUT_FOLDER}/{target_folder}/{top_dir}/{sub_rel}"
                dest_dir = str(Path(dest_path).parent).replace("\\", "/")
                op = self._make_copy_op(
                    source=source_rel,
                    destination=dest_path,
                    original_name=filename,
                    rename=rename,
                    reason=f"Part of dev project folder '{top_dir}'",
                    rule="dev_project_unit",
                    confidence_score=0.92,
                    flags=[],
                )
                if op:
                    self._ensure_dirs(dest_dir,
                                     operations, created_dirs,
                                     new_folders_proposed, existing_folders_reused)
                    new_folders_proposed, existing_folders_reused = self._queue_create_folder(
                        dest_dir, operations, created_dirs, root,
                        new_folders_proposed, existing_folders_reused
                    )
                    if self._try_record_dest(op.destination, source_rel, planned_dests, warnings):
                        if not output_folder_queued:
                            output_folder_queued, new_folders_proposed, existing_folders_reused = \
                                self._queue_output_root(operations, created_dirs, root,
                                                        new_folders_proposed, existing_folders_reused)
                        operations.append(op)
                continue

            # ── Sidecar: destination follows parent ────────────────────
            if norm_source in sidecar_map:
                parent_rel = sidecar_map[norm_source]
                # We'll handle this after the parent is processed; skip for now
                # and let the sidecar-follower pass below handle it.
                # Actually easier: process sidecar inline after parent is known.
                # We skip here; a second pass below will handle sidecars.
                continue

            # ── Duplicate loser → _Duplicates/ ────────────────────────
            dup_info = dup_loser_paths.get(norm_source)
            if dup_info:
                rel_dir = str(Path(norm_source).parent)
                if rel_dir == ".":
                    rel_dir = ""
                dup_dest_dir = f"{OUTPUT_FOLDER}/_Duplicates"
                if rel_dir:
                    dup_dest_dir += f"/{rel_dir}"
                dest_path = f"{dup_dest_dir}/{filename}"
                op = OrganizationOperation(
                    type="COPY_FILE",
                    source=source_rel,
                    destination=dest_path,
                    reason=f"Duplicate of '{dup_info['winner_name']}' (same SHA-256)",
                    confidence="LOW",
                    rule="duplicate_loser",
                    confidence_score=0.95,
                    flags="duplicate",
                    original_name=filename,
                    duplicate_of=dup_info["hash"],
                    needs_review=False,
                )
                new_folders_proposed, existing_folders_reused = self._queue_create_folder(
                    dup_dest_dir, operations, created_dirs, root,
                    new_folders_proposed, existing_folders_reused
                )
                if self._try_record_dest(dest_path, source_rel, planned_dests, warnings):
                    if not output_folder_queued:
                        output_folder_queued, new_folders_proposed, existing_folders_reused = \
                            self._queue_output_root(operations, created_dirs, root,
                                                    new_folders_proposed, existing_folders_reused)
                    operations.append(op)
                continue

            # ── Normal classification ──────────────────────────────────
            text_content: Optional[str] = text_contents.get(file_id)
            classification = classify_file(file_record, text_content=text_content)

            conf_score: float = classification["confidence"]
            conf_label: str = classification["confidence_label"]
            explanation: str = classification["explanation"]
            rule: str = classification["rule"]
            flags: List[str] = list(classification["flags"])
            topic: str = classification["topic"]
            needs_review: bool = classification["needs_review"]
            folder_path: str = classification["folder_path"]
            parts = folder_path.split("/")
            top_part = parts[0]
            if top_part not in ("_Review", "_Empty", "_Duplicates", "_Archive"):
                matched = match_existing_folder(top_part, existing_org_dirs)
                if matched:
                    parts[0] = matched
                    folder_path = "/".join(parts)

            # Track unclassified files
            if conf_score < 0.5 or folder_path == "_Review":
                unclassified_files += 1
            if topic:
                detected_topics[topic] = detected_topics.get(topic, 0) + 1

            # Apply archive rule
            if archive_days is not None:
                age_days = file_record.get("age_days") or 0
                if age_days > archive_days:
                    folder_path = "_Archive"
                    flags.append("archived")

            # Route low-confidence to _Review
            if conf_score < 0.5 and folder_path not in ("_Review", "_Empty"):
                folder_path = "_Review"
                flags.append("low_confidence")
                needs_review = True

            # ── Filename / date handling ──────────────────────────────
            date_str, date_source = (None, None)
            date_result = detect_filename_date(filename)
            if date_result:
                date_str, date_source = date_result

            original_name = filename
            final_name, _rename_reason = (filename, None)
            norm_result = normalize_filename(filename, rename=rename)
            if norm_result:
                final_name, _rename_reason = norm_result

            # Check likely_newer version variant
            likely_newer = detect_version_variant(filename)

            # ── Max-100 per folder: inject year/month bucket ──────────
            dest_folder = f"{OUTPUT_FOLDER}/{folder_path}"
            if folder_path not in ("_Review", "_Empty", "_Duplicates", "_Archive"):
                folder_item_counts[dest_folder] += 1
                if folder_item_counts[dest_folder] > MAX_ITEMS_PER_FOLDER:
                    # Split by year (from file date or modified_time)
                    year = None
                    if date_str:
                        year = date_str[:4]
                    if not year and file_record.get("modified_time"):
                        try:
                            year = file_record["modified_time"][:4]
                        except Exception:
                            pass
                    if year:
                        dest_folder = f"{dest_folder}/{year}"
                        folder_item_counts[dest_folder] += 1

            # ── Build destination path ─────────────────────────────────
            dest_path = f"{dest_folder}/{final_name}"
            dest_path = dest_path.replace("\\", "/")

            # Skip self-moves
            if norm_source == dest_path:
                continue

            # ── Conflict resolution ────────────────────────────────────
            abs_dest = root / dest_path
            other_source = planned_dests.get(dest_path)

            if other_source is not None:
                # Same destination already claimed — same name different content
                # Produce (2), (3), ... suffix
                n = 2
                new_dest = dest_path
                while new_dest in planned_dests:
                    candidate = make_dedup_suffix(final_name, n)
                    new_dest = f"{dest_folder}/{candidate}"
                    n += 1
                dest_path = new_dest
                abs_dest = root / dest_path
                flags.append("name_collision")
                if likely_newer:
                    flags.append("likely_newer")
            elif abs_dest.exists():
                # Destination exists on disk from previous run (idempotency)
                warnings.append(f"Destination already exists on disk: {dest_path}")
                continue
            elif likely_newer:
                flags.append("likely_newer")

            # Record occupancy
            planned_dests[dest_path] = source_rel

            # ── Ensure SmartSort_Organized root is queued ──────────────
            if not output_folder_queued:
                output_folder_queued, new_folders_proposed, existing_folders_reused = \
                    self._queue_output_root(operations, created_dirs, root,
                                            new_folders_proposed, existing_folders_reused)

            # ── Queue CREATE_FOLDER hierarchy ──────────────────────────
            folder_parts = folder_path.split("/")
            cumulative = OUTPUT_FOLDER
            for part in folder_parts:
                cumulative = f"{cumulative}/{part}"
                new_folders_proposed, existing_folders_reused = self._queue_create_folder(
                    cumulative, operations, created_dirs, root,
                    new_folders_proposed, existing_folders_reused
                )
            # Also queue the year-bucket subfolder if it was added
            if dest_folder != f"{OUTPUT_FOLDER}/{folder_path}":
                new_folders_proposed, existing_folders_reused = self._queue_create_folder(
                    dest_folder, operations, created_dirs, root,
                    new_folders_proposed, existing_folders_reused
                )

            # ── Emit operation ─────────────────────────────────────────
            reason = explanation or f"Grouped into '{folder_path}'"
            op = OrganizationOperation(
                type="COPY_FILE",
                source=source_rel,
                destination=dest_path,
                reason=reason,
                confidence=conf_label,
                explanation=explanation,
                folder_exists=(root / dest_folder).exists(),
                topic=topic,
                rule=rule,
                confidence_score=conf_score,
                flags=",".join(flags) if flags else None,
                date_source=date_source,
                original_name=original_name,
                duplicate_of=None,
                likely_newer=likely_newer if likely_newer else None,
                needs_review=needs_review,
            )
            operations.append(op)

        # ── Second pass: sidecars ──────────────────────────────────────
        # Find the planned destination of each sidecar's parent and put the
        # sidecar in the same folder.
        dest_for_source: Dict[str, str] = {v: k for k, v in planned_dests.items()}
        for file_record in sorted(files_data, key=lambda x: x.get("relative_path", "")):
            source_rel = file_record.get("relative_path", "")
            norm_source = source_rel.replace("\\", "/")
            if norm_source not in sidecar_map:
                continue
            filename = file_record.get("name", "")
            if is_partial_download(filename):
                continue
            parent_rel = sidecar_map[norm_source]
            parent_dest = dest_for_source.get(parent_rel)
            if parent_dest is None:
                # Parent wasn't planned; just use _Review for sidecar
                dest_folder = f"{OUTPUT_FOLDER}/_Review"
                final_name = filename
            else:
                dest_folder = str(Path(parent_dest).parent).replace("\\", "/")
                final_name = filename
            dest_path = f"{dest_folder}/{final_name}"
            if dest_path in planned_dests:
                n = 2
                while dest_path in planned_dests:
                    candidate = make_dedup_suffix(final_name, n)
                    dest_path = f"{dest_folder}/{candidate}"
                    n += 1
            planned_dests[dest_path] = norm_source
            if not output_folder_queued:
                output_folder_queued, new_folders_proposed, existing_folders_reused = \
                    self._queue_output_root(operations, created_dirs, root,
                                            new_folders_proposed, existing_folders_reused)
            op = OrganizationOperation(
                type="COPY_FILE",
                source=source_rel,
                destination=dest_path,
                reason=f"Sidecar companion for '{Path(parent_rel).name}'",
                confidence="HIGH",
                rule="sidecar_companion",
                confidence_score=0.90,
                flags="sidecar",
                original_name=filename,
                needs_review=False,
            )
            operations.append(op)

        # ── Sort: CREATE_FOLDER first ──────────────────────────────────
        operations.sort(
            key=lambda op: (0 if op.type == "CREATE_FOLDER" else 1, op.destination)
        )

        # ── Summary ───────────────────────────────────────────────────
        summary: Dict[str, int] = {}
        for op in operations:
            summary[op.type] = summary.get(op.type, 0) + 1

        return OrganizationPlan(
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
            structure=effective_structure,
            rename=rename,
            archive_days=archive_days,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _queue_output_root(
        self,
        operations: List[OrganizationOperation],
        created_dirs: Set[str],
        root: Path,
        new_folders_proposed: int,
        existing_folders_reused: int,
    ) -> Tuple[bool, int, int]:
        """Queue CREATE_FOLDER for SmartSort_Organized if not done yet.

        Returns (output_folder_queued=True, updated_new, updated_reused).
        """
        created_dirs.add(OUTPUT_FOLDER)
        output_abs = root / OUTPUT_FOLDER
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
                confidence_score=1.0,
                rule="system",
                needs_review=False,
            )
        )
        return True, new_folders_proposed, existing_folders_reused

    def _queue_create_folder(
        self,
        path: str,
        operations: List[OrganizationOperation],
        created_dirs: Set[str],
        root: Path,
        new_folders_proposed: int,
        existing_folders_reused: int,
    ) -> Tuple[int, int]:
        """Queue a CREATE_FOLDER operation if not already queued."""
        if path in created_dirs:
            return new_folders_proposed, existing_folders_reused
        created_dirs.add(path)
        abs_path = root / path
        exists = abs_path.exists()
        if exists:
            existing_folders_reused += 1
        else:
            new_folders_proposed += 1
        operations.append(
            OrganizationOperation(
                type="CREATE_FOLDER",
                source=path,
                destination=path,
                reason=f"Create folder '{path}'",
                confidence="HIGH",
                folder_exists=exists,
                confidence_score=1.0,
                rule="system",
                needs_review=False,
            )
        )
        return new_folders_proposed, existing_folders_reused

    def _ensure_dirs(
        self,
        folder_path: str,
        operations: List[OrganizationOperation],
        created_dirs: Set[str],
        new_folders_proposed: int,
        existing_folders_reused: int,
    ) -> None:
        """Queue CREATE_FOLDER for every level in folder_path."""
        root = self._authorized_root()
        parts = folder_path.split("/")
        cumulative = ""
        for part in parts:
            cumulative = f"{cumulative}/{part}" if cumulative else part
            new_folders_proposed, existing_folders_reused = self._queue_create_folder(
                cumulative, operations, created_dirs, root,
                new_folders_proposed, existing_folders_reused
            )

    def _make_copy_op(
        self,
        source: str,
        destination: str,
        original_name: str,
        rename: bool,
        reason: str,
        rule: str,
        confidence_score: float,
        flags: List[str],
    ) -> Optional[OrganizationOperation]:
        return OrganizationOperation(
            type="COPY_FILE",
            source=source,
            destination=destination,
            reason=reason,
            confidence="HIGH",
            rule=rule,
            confidence_score=confidence_score,
            flags=",".join(flags) if flags else None,
            original_name=original_name,
            needs_review=False,
        )

    def _try_record_dest(
        self,
        dest_path: str,
        source_rel: str,
        planned_dests: Dict[str, str],
        warnings: List[str],
    ) -> bool:
        """Return True if destination slot is free and record it; else warn and return False."""
        if dest_path in planned_dests:
            warnings.append(
                f"Conflict: Both '{source_rel}' and '{planned_dests[dest_path]}' "
                f"mapped to '{dest_path}'. Second operation skipped."
            )
            return False
        root = self._authorized_root()
        if (root / dest_path).exists():
            warnings.append(f"Destination already exists on disk: {dest_path}")
            return False
        planned_dests[dest_path] = source_rel
        return True
