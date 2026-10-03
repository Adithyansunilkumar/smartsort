"""Filesystem hashing service for Phase 4.

This module provides a read-only, incremental SHA-256 implementation for hashing
files. It operates only on canonical paths that have already been validated
by the FolderAccessService.

It is designed to handle very large files efficiently by reading in chunks,
and to gracefully manage filesystem race conditions (e.g., file disappeared).
"""

import hashlib
from pathlib import Path
from typing import Optional

# 64KB chunk size balances memory footprint with I/O overhead.
CHUNK_SIZE = 64 * 1024


def calculate_file_hash(path: Path) -> Optional[str]:
    """Calculate the SHA-256 cryptographic hash of a file incrementally.

    Args:
        path: A valid, canonical `Path` that has already been verified
            by `FolderAccessService`.

    Returns:
        The lowercase hexadecimal SHA-256 digest string, or `None` if the
        file could not be read (e.g., disappeared, permission denied, I/O error).

    Note:
        The implementation opens the file read-only, processes it in chunks
        without loading the entire file into memory, and guarantees the file
        is closed upon completion or failure.
    """
    hasher = hashlib.sha256()

    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        # File disappeared, permission denied, read failed, etc.
        # We explicitly return None rather than fabricating a hash.
        return None

    return hasher.hexdigest()
