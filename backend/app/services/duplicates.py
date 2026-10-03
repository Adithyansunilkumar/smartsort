"""Service for detecting exact duplicate files based on cryptographic hashes.

This module orchestrates Phase 4 duplicate detection by:
1. Fetching scanned files from the database.
2. Grouping them by size to isolate potential duplicates.
3. Securely validating file paths via `FolderAccessService`.
4. Hashing eligible candidates incrementally via `hasher.py`.
5. Persisting the hashes to the database.
6. Returning groups of identical files.
"""

from collections import defaultdict
from typing import Dict, List

from pydantic import BaseModel, Field

from app.database.repositories import ScanRepository
from app.filesystem.hasher import calculate_file_hash
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService


class DuplicateFile(BaseModel):
    """Metadata for a single duplicated file."""

    file_id: int = Field(..., description="The internal database ID of the file")
    name: str = Field(..., description="Basename of the file")
    relative_path: str = Field(
        ..., description="Forward-slash path relative to the authorized root"
    )
    size_bytes: int = Field(..., description="Size of the file in bytes")
    modified_at: str = Field(..., description="ISO-8601 UTC modification timestamp")


class DuplicateGroup(BaseModel):
    """A collection of identical files that share the exact same hash."""

    hash_digest: str = Field(
        ..., description="The lowercase hexadecimal SHA-256 digest of the files in this group"
    )
    size_bytes: int = Field(..., description="The exact size in bytes shared by all files")
    files: List[DuplicateFile] = Field(..., description="The list of identical files")


class DuplicateResult(BaseModel):
    """The complete result of a duplicate detection operation."""

    scan_id: str = Field(..., description="The UUID of the scan that was processed")
    total_groups: int = Field(
        ..., description="Total number of discrete duplicate groups discovered"
    )
    total_duplicate_files: int = Field(
        ..., description="Total number of individual duplicate files across all groups"
    )
    groups: List[DuplicateGroup] = Field(default_factory=list)


def find_duplicates(scan_id: str, folder_service: FolderAccessService) -> DuplicateResult:
    """Identify and group duplicate files for a given scan.

    Process:
    1. Retrieve all files belonging to the `scan_id`.
    2. Group files by exact size. Discard sizes that only occur once (singletons).
    3. For all files in the remaining size buckets that don't have a hash:
       - Validate their stored path against the `FolderAccessService`.
       - Calculate their SHA-256 hash securely.
       - Store the hash in the database.
    4. Group the hashed files by their exact SHA-256 digest.
    5. Discard singletons again.
    6. Return the strictly identical groups.

    Args:
        scan_id: The UUID of the scan.
        folder_service: The process-scoped `FolderAccessService` singleton.

    Returns:
        A `DuplicateResult` summarizing the exact duplicate groups.

    Raises:
        ValueError: If the scan_id produces no files (scan not found or empty).
        PathSecurityError: If an existing root is missing or access is invalid.
    """
    # 1. Fetch files from repository
    files_data = ScanRepository.get_files_by_scan_id(scan_id)
    if not files_data:
        raise ValueError(f"Scan '{scan_id}' not found or contains no files.")

    # 2. Group by size
    size_groups: Dict[int, List[Dict]] = defaultdict(list)
    for f in files_data:
        size_groups[f["size"]].append(f)

    # Filter out sizes with only 1 file (cannot be duplicates)
    potential_dupes = [group for group in size_groups.values() if len(group) > 1]

    hash_updates: List[tuple[int, str]] = []
    
    # Needs the root for relative->absolute path resolution during validation
    # This will raise PathSecurityError(ROOT_NOT_CONFIGURED) if not set.
    folder_service.require_authorized_root()

    # 3. Hash the eligible candidates if they lack a hash
    for size_group in potential_dupes:
        for f in size_group:
            # If we've already hashed it in a previous run, skip recalculation
            if f["hash"]:
                continue

            try:
                # Security Boundary Re-Validation:
                # Ensure the stored relative path still points to a valid file
                # strictly inside the CURRENT authorized root.
                canonical_path = folder_service.validate_path_in_root(
                    f["path"], allow_root=False
                )
            except PathSecurityError:
                # If the file path is now illegal (e.g., traversal, outside root),
                # skip it safely.
                continue

            # Calculate SHA-256 (handles I/O errors gracefully)
            file_hash = calculate_file_hash(canonical_path)
            if file_hash:
                f["hash"] = file_hash
                hash_updates.append((file_hash, f["id"]))

    # Update database with all newly computed hashes in bulk
    if hash_updates:
        ScanRepository.update_file_hashes(hash_updates)

    # 4. Group by hash
    hash_groups: Dict[str, List[Dict]] = defaultdict(list)
    for size_group in potential_dupes:
        for f in size_group:
            if f["hash"]:
                hash_groups[f["hash"]].append(f)

    # 5. Build final result (discarding hash singletons)
    final_groups = []
    for h_digest, group_files in hash_groups.items():
        if len(group_files) > 1:
            # Sort files by relative_path for deterministic output
            sorted_files = sorted(group_files, key=lambda x: x["path"].lower())
            
            dup_files = [
                DuplicateFile(
                    file_id=df["id"],
                    name=df["name"],
                    relative_path=df["path"],
                    size_bytes=df["size"],
                    modified_at=df["modified_time"],
                )
                for df in sorted_files
            ]
            
            final_groups.append(
                DuplicateGroup(
                    hash_digest=h_digest,
                    size_bytes=group_files[0]["size"],
                    files=dup_files,
                )
            )

    # Deterministic sorting for groups (by size descending, then hash ascending)
    final_groups.sort(key=lambda g: (-g.size_bytes, g.hash_digest))

    total_files = sum(len(g.files) for g in final_groups)

    return DuplicateResult(
        scan_id=scan_id,
        total_groups=len(final_groups),
        total_duplicate_files=total_files,
        groups=final_groups,
    )
