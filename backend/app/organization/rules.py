"""Organization Engine Rules & Constants.

All category lists, thresholds, synonyms, and deterministic helper functions
are centralised here so that the classifier and planner never hard-code values.

Content-aware folder generation is handled by the classifier module;
this module provides the data tables it consumes plus filename-level utilities.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Depth / size thresholds
# ---------------------------------------------------------------------------

MAX_DEPTH: int = 3          # Maximum folder levels below SmartSort_Organized
MIN_FILES_PER_FOLDER: int = 3  # Don't create a new subfolder for fewer files
MAX_ITEMS_PER_FOLDER: int = 100  # Split into sub-buckets above this

# ---------------------------------------------------------------------------
# Extension → category map  (case-insensitive; compound extensions matched whole)
# ---------------------------------------------------------------------------

# Compound extensions must be checked BEFORE single-suffix lookups.
COMPOUND_EXTENSION_MAP: Dict[str, str] = {
    ".tar.gz": "Archives",
    ".tar.bz2": "Archives",
    ".tar.xz": "Archives",
}

EXTENSION_MAP: Dict[str, str] = {
    # Documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".txt": "Documents", ".md": "Documents", ".rtf": "Documents",
    ".odt": "Documents",
    # Spreadsheets
    ".xls": "Spreadsheets", ".xlsx": "Spreadsheets", ".csv": "Spreadsheets",
    ".ods": "Spreadsheets",
    # Presentations
    ".ppt": "Presentations", ".pptx": "Presentations", ".key": "Presentations",
    ".odp": "Presentations",
    # Images
    ".jpg": "Images", ".jpeg": "Images", ".png": "Images", ".gif": "Images",
    ".webp": "Images", ".heic": "Images", ".svg": "Images",
    # Videos
    ".mp4": "Videos", ".mov": "Videos", ".mkv": "Videos", ".avi": "Videos",
    ".webm": "Videos",
    # Audio
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".m4a": "Audio",
    ".aac": "Audio",
    # Archives
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".tgz": "Archives",
    # Installers
    ".exe": "Installers", ".msi": "Installers", ".dmg": "Installers",
    ".pkg": "Installers", ".deb": "Installers", ".apk": "Installers",
    # Code / Development
    ".py": "Development", ".js": "Development", ".ts": "Development",
    ".java": "Development", ".c": "Development", ".cpp": "Development",
    ".html": "Development", ".css": "Development",
    # Data
    ".json": "Data", ".xml": "Data", ".sql": "Data", ".db": "Data",
    ".parquet": "Data",
    # Design
    ".psd": "Design", ".ai": "Design", ".fig": "Design",
    ".sketch": "Design", ".xd": "Design",
    # Ebooks
    ".epub": "Ebooks", ".mobi": "Ebooks",
    # Fonts
    ".ttf": "Fonts", ".otf": "Fonts", ".woff": "Fonts", ".woff2": "Fonts",
}

# Reverse lookup: category → set of extensions (built once at import time)
CATEGORY_EXTENSIONS: Dict[str, Set[str]] = {}
for _ext, _cat in EXTENSION_MAP.items():
    CATEGORY_EXTENSIONS.setdefault(_cat, set()).add(_ext)
for _ext, _cat in COMPOUND_EXTENSION_MAP.items():
    CATEGORY_EXTENSIONS.setdefault(_cat, set()).add(_ext)

# ---------------------------------------------------------------------------
# MIME families (for mismatch detection)
# ---------------------------------------------------------------------------

MIME_FAMILY_MAP: Dict[str, str] = {
    "image": "Images",
    "video": "Videos",
    "audio": "Audio",
    "application/pdf": "Documents",
    "application/zip": "Archives",
    "application/x-rar-compressed": "Archives",
    "application/x-7z-compressed": "Archives",
    "application/gzip": "Archives",
    "application/x-tar": "Archives",
    "application/vnd.ms-excel": "Spreadsheets",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheets",
    "application/msword": "Documents",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Documents",
    "application/vnd.ms-powerpoint": "Presentations",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentations",
    "text": "Documents",
}

# ---------------------------------------------------------------------------
# Partial-download extensions — skip these in scanner/planner
# ---------------------------------------------------------------------------

PARTIAL_DOWNLOAD_EXTS: FrozenSet[str] = frozenset({
    ".crdownload", ".part", ".tmp", ".download",
})

# ---------------------------------------------------------------------------
# Sidecar / companion extensions
# ---------------------------------------------------------------------------

SIDECAR_EXTS: FrozenSet[str] = frozenset({
    ".xmp", ".aae", ".srt", ".vtt", ".lrc",
})

# ---------------------------------------------------------------------------
# Dev-project markers — if a folder contains any of these it is a project unit
# ---------------------------------------------------------------------------

DEV_PROJECT_MARKERS: FrozenSet[str] = frozenset({
    ".git", "package.json", "pyproject.toml", "Cargo.toml", "Makefile",
})

DEV_PROJECT_GLOB_MARKERS: Tuple[str, ...] = ("*.sln", "*.csproj")

# ---------------------------------------------------------------------------
# Folder-name synonyms  (canonical name → known aliases, lowercase)
# ---------------------------------------------------------------------------

FOLDER_SYNONYMS: Dict[str, FrozenSet[str]] = {
    "Images":        frozenset({"pics", "pictures", "photos", "images", "img"}),
    "Documents":     frozenset({"docs", "documents", "doc"}),
    "Videos":        frozenset({"vids", "movies", "videos", "video"}),
    "Audio":         frozenset({"music", "audio", "songs", "sounds"}),
    "Spreadsheets":  frozenset({"sheets", "spreadsheets"}),
}

# Inverted lookup: alias → canonical (built once)
SYNONYM_TO_CANONICAL: Dict[str, str] = {}
for _canon, _aliases in FOLDER_SYNONYMS.items():
    for _alias in _aliases:
        SYNONYM_TO_CANONICAL[_alias] = _canon

# ---------------------------------------------------------------------------
# Duplicate "noise" words — penalise these when picking the clearest name
# ---------------------------------------------------------------------------

DUPLICATE_NOISE_WORDS: FrozenSet[str] = frozenset({
    "copy", "final", "final2", "new", "untitled",
})

DUPLICATE_NOISE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\(\d+\)"),       # (1), (2), …
    re.compile(r"_v\d+", re.I),   # _v2, _V3
    re.compile(r" - copy", re.I),
)

# ---------------------------------------------------------------------------
# Version / newer-variant patterns
# ---------------------------------------------------------------------------

VERSION_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"_v(\d+)", re.I),
    re.compile(r"_final", re.I),
    re.compile(r" v(\d+)", re.I),
    re.compile(r"\((\d+)\)"),
)

# ---------------------------------------------------------------------------
# Illegal filename characters (Windows FS restriction set)
# ---------------------------------------------------------------------------

ILLEGAL_NAME_CHARS: re.Pattern = re.compile(r'[<>:"/\\|?*]')

# ---------------------------------------------------------------------------
# Date-sensitive categories (prefix YYYY-MM-DD_ when rename is on)
# ---------------------------------------------------------------------------

DATE_SENSITIVE_CATEGORIES: FrozenSet[str] = frozenset({
    "Documents/Financial", "Images/Photos",
})

DATE_SENSITIVE_KEYWORDS: FrozenSet[str] = frozenset({
    "invoice", "receipt", "statement", "bill", "tax",
})

# ---------------------------------------------------------------------------
# Name-hint patterns  (checked BEFORE extension map)
#   Each: (compiled_regex_on_lowered_name, folder_path, confidence, rule_name)
# ---------------------------------------------------------------------------

NAME_HINT_PATTERNS: List[Tuple[re.Pattern, str, float, str]] = [
    # Financial
    (re.compile(r"(?:^|[_\- ])invoice", re.I),   "Documents/Financial", 0.92, "name_invoice"),
    (re.compile(r"(?:^|[_\- ])receipt", re.I),    "Documents/Financial", 0.92, "name_receipt"),
    (re.compile(r"(?:^|[_\- ])bill(?:$|[_\- ])", re.I), "Documents/Financial", 0.90, "name_bill"),
    (re.compile(r"(?:^|[_\- ])statement", re.I),  "Documents/Financial", 0.90, "name_statement"),
    (re.compile(r"(?:^|[_\- ])tax(?:$|[_\- ])", re.I), "Documents/Financial", 0.90, "name_tax"),
    # Legal
    (re.compile(r"(?:^|[_\- ])contract", re.I),   "Documents/Legal", 0.90, "name_contract"),
    (re.compile(r"(?:^|[_\- ])agreement", re.I),  "Documents/Legal", 0.90, "name_agreement"),
    (re.compile(r"(?:^|[_\- ])nda(?:$|[_\- ])", re.I), "Documents/Legal", 0.90, "name_nda"),
    (re.compile(r"(?:^|[_\- ])terms(?:$|[_\- ])", re.I), "Documents/Legal", 0.88, "name_terms"),
    # Career
    (re.compile(r"(?:^|[_\- ])resume", re.I),     "Documents/Career", 0.92, "name_resume"),
    (re.compile(r"(?:^|[_\- ])(?:cv|curriculum[\s_-]*vitae)", re.I), "Documents/Career", 0.92, "name_cv"),
    (re.compile(r"cover[\s_-]*letter", re.I),      "Documents/Career", 0.88, "name_cover_letter"),
    # Screenshots / Screen recordings
    (re.compile(r"screen[\s_-]*shot", re.I),       "Images/Screenshots", 0.92, "name_screenshot"),
    (re.compile(r"screenshot", re.I),              "Images/Screenshots", 0.92, "name_screenshot"),
    (re.compile(r"screen[\s_-]*recording", re.I),  "Videos/Screen Recordings", 0.92, "name_screen_recording"),
    # Photo camera prefixes  (only when paired with an image extension — checked by caller)
    (re.compile(r"^(?:IMG|DSC|PXL)_", re.I),       "Images/Photos", 0.85, "name_camera_prefix"),
]

# ---------------------------------------------------------------------------
# Content-hint keywords  (Tier 3 supporting evidence from extracted text)
# ---------------------------------------------------------------------------

CONTENT_HINT_KEYWORDS: Dict[str, Tuple[str, float, str]] = {
    # keyword → (folder_path, confidence, rule_name)
    "invoice":   ("Documents/Financial", 0.80, "content_invoice"),
    "receipt":   ("Documents/Financial", 0.80, "content_receipt"),
    "bill":      ("Documents/Financial", 0.75, "content_bill"),
    "statement": ("Documents/Financial", 0.75, "content_statement"),
    "tax":       ("Documents/Financial", 0.75, "content_tax"),
    "contract":  ("Documents/Legal",     0.80, "content_contract"),
    "agreement": ("Documents/Legal",     0.80, "content_agreement"),
    "nda":       ("Documents/Legal",     0.78, "content_nda"),
    "terms":     ("Documents/Legal",     0.72, "content_terms"),
    "resume":    ("Documents/Career",    0.80, "content_resume"),
    "cv":        ("Documents/Career",    0.75, "content_cv"),
}

# ---------------------------------------------------------------------------
# Stop-words – tokens that carry no topical meaning (kept from original)
# ---------------------------------------------------------------------------

STOP_WORDS: FrozenSet[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "by", "is", "it", "its", "be", "as", "are", "was", "were", "from",
    "with", "this", "that", "these", "those", "my", "our", "your", "has",
    "have", "had", "not", "but", "do", "did", "does", "will", "would",
    "can", "could", "should", "about", "into", "than", "then", "so",
    "we", "they", "he", "she", "you", "me", "him", "her", "us",
    "new", "old", "all", "also", "any", "each", "file", "files", "doc",
    "document", "docs", "image", "images", "photo", "video", "copy",
    "final", "draft", "version", "ver", "rev", "update", "updated",
    "temp", "tmp", "test", "backup", "bak", "misc", "other", "stuff",
    "sample", "example", "untitled", "na",
    "data", "science", "content", "info", "information",
})

# Project-grouping stop words (also drop during tokenisation for project detection)
PROJECT_STOP_TOKENS: FrozenSet[str] = frozenset({
    "final", "copy", "v1", "v2", "v3", "draft", "new", "old",
    "backup", "bak", "temp", "tmp",
})

# ---------------------------------------------------------------------------
# Filename date extraction
# ---------------------------------------------------------------------------

_DATE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # YYYY-MM-DD  or  YYYY_MM_DD
    (re.compile(r"(20\d{2})[-_](0[1-9]|1[0-2])[-_](0[1-9]|[12]\d|3[01])"), "filename_date"),
    # YYYYMMDD
    (re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"), "filename_date"),
    # YYYY-MM  or  YYYY_MM  (day-less)
    (re.compile(r"(20\d{2})[-_](0[1-9]|1[0-2])(?!\d)"), "filename_month"),
    # Bare year
    (re.compile(r"(?<!\d)(20\d{2})(?!\d)"), "filename_year"),
]


def detect_filename_date(name: str) -> Optional[Tuple[str, str]]:
    """Extract the best date string and source tag from a filename.

    Returns (date_str, date_source) or None.
    ``date_str`` is ``YYYY-MM-DD``, ``YYYY-MM``, or ``YYYY``.
    """
    stem = re.sub(r"\.[^.]+$", "", name)  # strip extension
    for pat, source in _DATE_PATTERNS:
        m = pat.search(stem)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                return f"{groups[0]}-{groups[1]}-{groups[2]}", source
            if len(groups) == 2:
                return f"{groups[0]}-{groups[1]}", source
            return groups[0], source
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_extension_category(filename: str) -> Optional[str]:
    """Return the spec category for a filename by extension lookup.

    Checks compound extensions first, then single suffix.
    """
    lower = filename.lower()
    for compound, cat in COMPOUND_EXTENSION_MAP.items():
        if lower.endswith(compound):
            return cat
    suffix = Path(filename).suffix.lower()
    return EXTENSION_MAP.get(suffix)


def get_mime_category(mime_type: Optional[str]) -> Optional[str]:
    """Map a MIME type string to its spec category, or None."""
    if not mime_type:
        return None
    # Exact match first
    cat = MIME_FAMILY_MAP.get(mime_type)
    if cat:
        return cat
    # Family prefix (e.g. "image/png" → family "image")
    family = mime_type.split("/")[0]
    return MIME_FAMILY_MAP.get(family)


def check_extension_mismatch(filename: str, mime_type: Optional[str]) -> bool:
    """Return True if extension-based category disagrees with MIME-based category."""
    ext_cat = get_extension_category(filename)
    mime_cat = get_mime_category(mime_type)
    if ext_cat is None or mime_cat is None:
        return False
    return ext_cat != mime_cat


def is_partial_download(filename: str) -> bool:
    """True if the file is a partial / in-progress download."""
    suffix = Path(filename).suffix.lower()
    return suffix in PARTIAL_DOWNLOAD_EXTS


def is_sidecar(filename: str) -> bool:
    """True if the file is a media sidecar / companion."""
    suffix = Path(filename).suffix.lower()
    return suffix in SIDECAR_EXTS


def is_dev_project_folder(folder_path: Path) -> bool:
    """Check whether a directory contains development-project markers."""
    if not folder_path.is_dir():
        return False
    for marker in DEV_PROJECT_MARKERS:
        if (folder_path / marker).exists():
            return True
    for glob_pat in DEV_PROJECT_GLOB_MARKERS:
        if any(folder_path.glob(glob_pat)):
            return True
    return False


def resolve_synonym(folder_name: str) -> str:
    """Return canonical category name if folder_name is a known synonym."""
    return SYNONYM_TO_CANONICAL.get(folder_name.lower(), folder_name)


def match_existing_folder(candidate: str, existing_dirs: Set[str]) -> Optional[str]:
    """Find a case-insensitive or synonym match in *existing_dirs*.

    Returns the existing folder name to reuse, or None.
    """
    candidate_lower = candidate.lower()
    canonical = SYNONYM_TO_CANONICAL.get(candidate_lower, candidate)
    canonical_lower = canonical.lower()

    for d in existing_dirs:
        d_lower = d.lower()
        if d_lower == candidate_lower or d_lower == canonical_lower:
            return d
        # Check if existing dir is a synonym of the canonical name
        d_canon = SYNONYM_TO_CANONICAL.get(d_lower)
        if d_canon and d_canon.lower() == canonical_lower:
            return d
    return None


def normalize_filename(filename: str, rename: bool = False) -> Optional[Tuple[str, str]]:
    """Propose a normalized filename if the current one has issues.

    When *rename* is False (the default), only critical fixes are applied:
    ``Copy of`` prefix removal, whitespace collapse.

    When *rename* is True, additional fixes are applied:
    illegal-character replacement, extension lowercasing, double-space collapse,
    leading/trailing dots and spaces trimmed.

    Returns (new_name, reason) or None if no change needed.
    """
    new_name = filename
    reasons: List[str] = []

    # ── Always applied (even when rename=False) ─────────────────────
    # Remove "Copy of " or "Copy (1) of " etc.
    copy_pattern = r"^Copy(\s*\(\d+\))?\s*of\s+(.+)$"
    if match := re.match(copy_pattern, new_name, re.IGNORECASE):
        new_name = match.group(2)
        reasons.append("Removed 'Copy of' prefix")

    # Normalize excessive spaces
    if "  " in new_name:
        new_name = re.sub(r"\s+", " ", new_name).strip()
        if not reasons:
            reasons.append("Normalized whitespace")

    # ── Only when rename=True ───────────────────────────────────────
    if rename:
        # Fix illegal characters
        if ILLEGAL_NAME_CHARS.search(new_name):
            # Separate stem and extension
            stem, ext = _split_name_ext(new_name)
            stem = ILLEGAL_NAME_CHARS.sub("-", stem)
            new_name = stem + ext
            reasons.append("Replaced illegal characters")

        # Lowercase extension (preserve compound)
        stem, ext = _split_name_ext(new_name)
        if ext and ext != ext.lower():
            new_name = stem + ext.lower()
            reasons.append("Lowercased extension")

        # Collapse double spaces again (in case illegal-char fix introduced them)
        new_name = re.sub(r"\s+", " ", new_name).strip()

        # Trim leading/trailing dots and spaces from stem
        stem, ext = _split_name_ext(new_name)
        stripped = stem.strip(". ")
        if stripped != stem:
            new_name = stripped + ext
            reasons.append("Trimmed leading/trailing dots and spaces")

    # 255-char enforcement
    if len(new_name.encode("utf-8")) > 255:
        stem, ext = _split_name_ext(new_name)
        max_stem = 255 - len(ext.encode("utf-8"))
        while len((stem + ext).encode("utf-8")) > 255 and stem:
            stem = stem[:-1]
        new_name = stem + ext
        reasons.append("Truncated to 255 bytes")

    if new_name != filename:
        return new_name, "; ".join(reasons)
    return None


def _split_name_ext(name: str) -> Tuple[str, str]:
    """Split a filename preserving compound extensions like .tar.gz."""
    lower = name.lower()
    for compound in COMPOUND_EXTENSION_MAP:
        if lower.endswith(compound):
            return name[: len(name) - len(compound)], name[len(name) - len(compound):]
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:]


def score_name_clarity(name: str) -> int:
    """Score a filename for clarity (higher = clearer).

    Penalise noise words and copy patterns.
    """
    score = 100
    lower = name.lower()
    for word in DUPLICATE_NOISE_WORDS:
        if word in lower:
            score -= 15
    for pat in DUPLICATE_NOISE_PATTERNS:
        if pat.search(lower):
            score -= 10
    # Bonus for longer, more descriptive names (up to a point)
    stem = re.sub(r"\.[^.]+$", "", name)
    score += min(len(stem), 30)
    return score


def detect_version_variant(name: str) -> bool:
    """Return True if the filename looks like a newer version variant."""
    for pat in VERSION_PATTERNS:
        if pat.search(name):
            return True
    return False


def make_dedup_suffix(base: str, n: int) -> str:
    """Produce 'base (2).ext' style name for collision n >= 2."""
    stem, ext = _split_name_ext(base)
    return f"{stem} ({n}){ext}"


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


def tokenize_for_project(name: str) -> List[str]:
    """Tokenize a filename for project-grouping: split on delimiters, drop
    numbers, dates, and stop tokens."""
    stem = re.sub(r"\.[^.]+$", "", name)
    tokens = re.findall(r"[a-zA-Z]{2,}", stem)
    result = []
    for t in tokens:
        low = t.lower()
        if low in PROJECT_STOP_TOKENS or low in STOP_WORDS:
            continue
        result.append(low)
    return result
