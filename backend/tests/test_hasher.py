"""Tests for the filesystem hashing service."""

import hashlib
from pathlib import Path
from unittest.mock import patch

from app.filesystem.hasher import calculate_file_hash, CHUNK_SIZE


def test_calculate_file_hash_empty_file(tmp_path: Path):
    """An empty file should produce the standard SHA-256 for empty input."""
    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")

    expected_hash = hashlib.sha256(b"").hexdigest()
    assert calculate_file_hash(empty_file) == expected_hash


def test_calculate_file_hash_text_file(tmp_path: Path):
    """A standard text file should hash correctly."""
    text_file = tmp_path / "hello.txt"
    content = b"Hello, World!"
    text_file.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()
    assert calculate_file_hash(text_file) == expected_hash


def test_calculate_file_hash_binary_file(tmp_path: Path):
    """A binary file should hash correctly."""
    bin_file = tmp_path / "data.bin"
    content = bytes(range(256)) * 10
    bin_file.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()
    assert calculate_file_hash(bin_file) == expected_hash


def test_calculate_file_hash_chunking(tmp_path: Path):
    """Verify that a file larger than CHUNK_SIZE is read in pieces."""
    large_file = tmp_path / "large.bin"
    # Create a file slightly larger than 2 chunks
    content = b"a" * (CHUNK_SIZE * 2 + 1024)
    large_file.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()

    # Track how many times `f.read()` is called
    read_calls = 0

    original_open = open

    def mock_open(*args, **kwargs):
        f = original_open(*args, **kwargs)
        original_read = f.read

        def tracking_read(*r_args, **r_kwargs):
            nonlocal read_calls
            read_calls += 1
            return original_read(*r_args, **r_kwargs)

        f.read = tracking_read
        return f

    with patch("builtins.open", side_effect=mock_open):
        result = calculate_file_hash(large_file)

    assert result == expected_hash
    # Should read: Chunk 1, Chunk 2, Chunk 3 (1024 bytes), Chunk 4 (empty string -> break)
    assert read_calls == 4


def test_calculate_file_hash_missing_file(tmp_path: Path):
    """A file that does not exist should return None, not crash."""
    missing = tmp_path / "gone.txt"
    assert calculate_file_hash(missing) is None


def test_calculate_file_hash_permission_denied(tmp_path: Path):
    """An IOError (like PermissionError) should return None."""
    target = tmp_path / "denied.txt"
    target.write_text("secret")

    with patch("builtins.open", side_effect=PermissionError("Access Denied")):
        assert calculate_file_hash(target) is None


def test_calculate_file_hash_read_only_mode(tmp_path: Path):
    """The file must be opened strictly in read-only binary mode ('rb')."""
    target = tmp_path / "check_mode.txt"
    target.write_text("content")

    original_open = open

    def mock_open(file, mode="r", *args, **kwargs):
        assert mode == "rb"
        return original_open(file, mode, *args, **kwargs)

    with patch("builtins.open", side_effect=mock_open):
        calculate_file_hash(target)


def test_calculate_file_hash_unchanged_metadata(tmp_path: Path):
    """Hashing a file must not modify its content or basic metadata."""
    target = tmp_path / "integrity.txt"
    content = b"Integrity check"
    target.write_bytes(content)

    stat_before = target.stat()
    
    calculate_file_hash(target)
    
    stat_after = target.stat()
    
    assert target.read_bytes() == content
    assert stat_after.st_size == stat_before.st_size
    assert stat_after.st_mtime == stat_before.st_mtime
