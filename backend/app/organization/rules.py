"""Phase 8 Organization Engine Rules.

Deterministic functions that consume a file record and produce a 
proposed organization action or warning.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import re


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
    existing_dirs: set[str],
) -> Optional[str]:
    """Determine the best target folder for a file.

    1. Uses the 'category' from Phase 6 analysis (e.g., 'document', 'image').
    2. Capitalizes it (e.g., 'Documents', 'Images').
    3. Checks if an exact or lower-case match already exists in existing_dirs.
    4. If not, proposes creating the capitalized folder name at the root.

    Returns the proposed relative directory path, or None if it's already in the right place.
    """
    category = file_record.get("category")
    if not category or category == "other":
        return None

    # e.g., 'document' -> 'Documents'
    target_dir_name = category.capitalize()
    if not target_dir_name.endswith("s"):
        target_dir_name += "s"

    # If it's already exactly inside that folder (at the root level)
    # parent_dir is stored in the analysis table
    parent_dir = file_record.get("parent_dir")
    is_root_level = file_record.get("is_root_level", False)
    
    # We want things like `Documents/file.pdf`.
    # If the file's path is `Documents/file.pdf`, then its parent is `Documents`
    # and it is one level deep.
    current_relative_path = file_record.get("relative_path", "")
    current_parent_path = str(Path(current_relative_path).parent)
    if current_parent_path == ".":
        current_parent_path = ""

    # Check if the current parent path matches the target (case-insensitive)
    if current_parent_path.lower() == target_dir_name.lower():
        return None

    # Check if an existing directory matches the target name exactly or case-insensitively
    # We prefer to use an existing root-level folder that matches
    for d in existing_dirs:
        if d.lower() == target_dir_name.lower():
            target_dir_name = d
            break

    return target_dir_name


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
