"""Rules and deterministic functions for File Analysis (Phase 6)."""

import re
from typing import List, Optional, Tuple
from datetime import datetime, timezone
import math

# Analysis Version - increment when logic changes to invalidate stale caches
ANALYSIS_VERSION = 1


def get_file_category(extension: str, mime_type: Optional[str]) -> str:
    """Determine file category deterministically based on extension and mime_type."""
    ext = extension.lower()
    
    docs = {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md"}
    spreadsheets = {".xls", ".xlsx", ".csv", ".ods", ".tsv"}
    presentations = {".ppt", ".pptx", ".odp"}
    images = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff"}
    audio = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
    video = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm"}
    archives = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}
    executables = {".exe", ".msi", ".bat", ".cmd", ".sh", ".bin", ".apk", ".app"}
    code = {".py", ".js", ".ts", ".html", ".css", ".java", ".c", ".cpp", ".h", ".cs", ".json", ".xml", ".yaml", ".yml", ".sql"}
    
    if ext in docs:
        return "document"
    if ext in spreadsheets:
        return "spreadsheet"
    if ext in presentations:
        return "presentation"
    if ext in images:
        return "image"
    if ext in audio:
        return "audio"
    if ext in video:
        return "video"
    if ext in archives:
        return "archive"
    if ext in executables:
        return "executable"
    if ext in code:
        return "code/source"
        
    if mime_type:
        if mime_type.startswith("text/plain"):
            return "plain text"
        if mime_type.startswith("text/"):
            return "document"
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("audio/"):
            return "audio"
        if mime_type.startswith("video/"):
            return "video"
            
    return "unknown"


def get_age_metrics(modified_time_iso: str, reference_time_iso: str) -> Tuple[int, str]:
    """Calculate age in days and age bucket deterministically."""
    mod_time = datetime.fromisoformat(modified_time_iso)
    ref_time = datetime.fromisoformat(reference_time_iso)
    
    delta = ref_time - mod_time
    age_days = max(0, delta.days)  # Avoid negative age
    
    if age_days <= 7:
        bucket = "very_recent"
    elif age_days <= 30:
        bucket = "recent"
    elif age_days <= 180:
        bucket = "older"
    elif age_days <= 365:
        bucket = "old"
    else:
        bucket = "very_old"
        
    return age_days, bucket


def get_size_bucket(size_bytes: int) -> str:
    """Determine size bucket."""
    mb = 1024 * 1024
    gb = 1024 * mb
    
    if size_bytes < mb:
        return "tiny"
    if size_bytes < 10 * mb:
        return "small"
    if size_bytes < 100 * mb:
        return "medium"
    if size_bytes < gb:
        return "large"
    return "very_large"


def parse_filename(name: str, extension: str) -> Tuple[List[str], Optional[int], bool, bool]:
    """
    Parse filename to extract tokens, numeric suffix, and flags (is_copy, is_temp).
    Returns: (tokens, numeric_suffix, is_copy, is_temp)
    """
    if extension and name.lower().endswith(extension.lower()):
        stem = name[:-len(extension)]
    else:
        stem = name
        
    # Tokens: split by non-alphanumeric, filter empty. Treat letter/number transitions as boundaries.
    # A simple way is to find all contiguous letters or numbers
    tokens = [t.lower() for t in re.findall(r'[a-zA-Z]+|[0-9]+', stem)]
    
    # Numeric suffix
    numeric_suffix = None
    suffix_match = re.search(r'(\d+)$', stem)
    if suffix_match:
        numeric_suffix = int(suffix_match.group(1))
        
    # is_copy pattern: " (1)", "- Copy", "_copy", "_2"
    is_copy = False
    lower_stem = stem.lower()
    if re.search(r' copy$| copy \(\d+\)$', lower_stem) or \
       re.search(r'_copy$|-copy$', lower_stem) or \
       re.search(r' \(\d+\)$', lower_stem):
        is_copy = True
        
    # is_temp pattern: starts with ~$ or ends with .tmp
    is_temp = False
    if name.startswith('~$') or name.startswith('.~') or name.lower().endswith('.tmp'):
        is_temp = True
        
    return tokens, numeric_suffix, is_copy, is_temp


def analyze_path(relative_path: str) -> Tuple[int, bool, Optional[str], List[str]]:
    """
    Analyze relative path.
    Returns: (depth, is_root_level, parent_dir, path_tokens)
    """
    # Normalize path separators
    path = relative_path.replace('\\', '/')
    parts = path.split('/')
    
    depth = len(parts) - 1
    is_root_level = depth == 0
    parent_dir = parts[-2] if depth > 0 else None
    
    # Path tokens (excluding the filename itself)
    path_tokens = []
    for p in parts[:-1]:
        path_tokens.extend([t.lower() for t in re.split(r'[^a-zA-Z0-9]+', p) if t])
        
    return depth, is_root_level, parent_dir, path_tokens


def analyze_content(text_content: Optional[str]) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """
    Analyze text content.
    Returns: (char_count, word_count, line_count, reading_time_mins)
    """
    if not text_content:
        return None, None, None, None
        
    char_count = len(text_content)
    words = text_content.split()
    word_count = len(words)
    lines = text_content.splitlines()
    line_count = len(lines)
    
    # 250 WPM reading speed
    reading_time_mins = math.ceil(word_count / 250) if word_count > 0 else 0
    
    return char_count, word_count, line_count, reading_time_mins
