"""Phase 8 Organization Engine Rules.

Deterministic functions that consume a file record and produce a
proposed organization action or warning.

Content-aware folder generation is handled by the classifier module;
this module provides filename normalization, warning generation, and
thin wrappers that delegate to the classifier.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import re

from app.organization.classifier import classify_file, ClassificationResult


def normalize_filename(filename: str) -> Optional[Tuple[str, str]]:
    """Propose a normalized filename if the current one has issues.

    Checks for:
    - "Copy of " prefixes
    - excessive whitespaces

    Returns (new_name, reason) or None if no change needed.
    """
    new_name = filename
    reason = None

    # Remove "Copy of " or "Copy (1) of " etc.
    copy_pattern = r"^Copy(\s*\(\d+\))?\s*of\s+(.+)$"
    if match := re.match(copy_pattern, new_name, re.IGNORECASE):
        new_name = match.group(2)
        reason = "Removed 'Copy of' prefix"

    # Normalize excessive spaces
    if "  " in new_name:
        new_name = re.sub(r"\s+", " ", new_name).strip()
        if not reason:
            reason = "Normalized whitespace"

    if new_name != filename:
        return new_name, reason
    return None


def get_proposed_folder(
    file_record: Dict[str, Any],
    existing_dirs: set,
    text_content: Optional[str] = None,
) -> Optional[str]:
    """Determine the best target folder for a file using content-aware classification.

    1. Runs the content-aware classifier to detect a specific topic/subject.
    2. If the classifier finds a meaningful match, uses that hierarchical path.
    3. Falls back to broad category-based folder when confidence is LOW and no
       specific match was found.
    4. Returns None if the file is already in the correct destination folder.

    Args:
        file_record: Combined files+file_analysis DB row.
        existing_dirs: Set of existing directory names at the root level
            (used for case-insensitive matching).
        text_content: Optionally pre-fetched extracted text for the file.

    Returns:
        Proposed relative directory path, or None if already correct.
    """
    result: ClassificationResult = classify_file(
        file_record,
        text_content=text_content,
    )
    target_folder: str = result["folder_path"]

    # Check if the file is already inside the correct destination
    current_relative_path = file_record.get("relative_path", "")
    current_parent_path = str(Path(current_relative_path).parent)
    if current_parent_path == ".":
        current_parent_path = ""

    # Normalize for comparison (use the top-level component only for existing-dir check)
    top_level = target_folder.split("/")[0]
    norm_current = current_parent_path.replace("\\", "/")

    # If file is already at the proposed destination, skip
    if norm_current.lower() == target_folder.lower():
        return None

    # Prefer an existing directory that case-insensitively matches the top level
    for d in existing_dirs:
        if d.lower() == top_level.lower() and "/" not in target_folder:
            # Single-level folder — reuse the existing cased name
            return d

    return target_folder


def get_proposed_folder_with_classification(
    file_record: Dict[str, Any],
    existing_dirs: set,
    text_content: Optional[str] = None,
) -> Tuple[Optional[str], ClassificationResult]:
    """Like get_proposed_folder but also returns the full classification result.

    Used by the planner so it can attach explanation, confidence, and topic
    to the operation without running the classifier twice.
    """
    result: ClassificationResult = classify_file(
        file_record,
        text_content=text_content,
    )
    target_folder: str = result["folder_path"]

    current_relative_path = file_record.get("relative_path", "")
    current_parent_path = str(Path(current_relative_path).parent)
    if current_parent_path == ".":
        current_parent_path = ""

    norm_current = current_parent_path.replace("\\", "/")
    if norm_current.lower() == target_folder.lower():
        return None, result

    top_level = target_folder.split("/")[0]
    for d in existing_dirs:
        if d.lower() == top_level.lower() and "/" not in target_folder:
            return d, result

    return target_folder, result


def generate_warnings(file_record: Dict[str, Any]) -> List[str]:
    """Generate warnings for duplicates or old/large files."""
    warnings = []

    name = file_record.get("name", "Unknown file")

    if file_record.get("has_duplicates"):
        warnings.append(f"File '{name}' has duplicates in the scan.")

    size_bucket = file_record.get("size_bucket")
    if size_bucket in ("huge", "giant"):
        warnings.append(f"File '{name}' is unusually large ({size_bucket}).")

    age_bucket = file_record.get("age_bucket")
    if age_bucket in ("old", "ancient"):
        warnings.append(f"File '{name}' is very old ({age_bucket}).")

    return warnings
