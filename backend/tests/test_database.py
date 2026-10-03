"""Tests for database initialization and schema."""

import os
from pathlib import Path
from app.database.schema import init_db
from app.database.connection import get_connection


def test_init_db_creates_file_and_schema(tmp_path: Path):
    """Test that init_db creates the database file and required tables."""
    db_file = tmp_path / "test.db"
    
    # Run init_db with the temporary path
    init_db(db_file)
    
    # Check that file exists
    assert db_file.exists()
    
    # Check that tables exist
    with get_connection(db_file) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row["name"] for row in cursor.fetchall()}
        
        assert "scans" in tables
        assert "files" in tables
        assert "protected_paths" in tables
        assert "operations" in tables
        assert "preferences" in tables


def test_init_db_is_idempotent(tmp_path: Path):
    """Test that init_db can be called multiple times without errors."""
    db_file = tmp_path / "test.db"
    
    init_db(db_file)
    # Should not raise an error
    init_db(db_file)
    init_db(db_file)
    
    # Tables should still be there
    with get_connection(db_file) as conn:
        cursor = conn.execute("SELECT count(*) as count FROM sqlite_master WHERE type='table'")
        count = cursor.fetchone()["count"]
        # sqlite_sequence might also exist due to AUTOINCREMENT, so count >= 5
        assert count >= 5
