"""Shared pytest fixtures for the SmartSort backend test suite."""

from pathlib import Path
from unittest.mock import patch
import pytest

from app.services.folder_access import get_folder_access_service, FolderAccessService
from app.database.schema import init_db


@pytest.fixture(autouse=True)
def clean_service() -> "pytest.Generator[FolderAccessService, None, None]":
    """Yield a cleared FolderAccessService singleton before each test.

    Clears the authorized root both before the test (to isolate it from any
    prior state) and after (to prevent leakage into subsequent tests).
    """
    service = get_folder_access_service()
    service.clear_authorized_root()
    yield service
    service.clear_authorized_root()


@pytest.fixture()
def authorized_root(tmp_path: Path, clean_service: FolderAccessService) -> Path:
    """Create a temporary directory and set it as the authorized root.

    Returns the canonical Path of the newly authorized root so tests can
    reference it directly without calling the service themselves.
    """
    root_dir = tmp_path / "authorized_root"
    root_dir.mkdir()
    canonical = root_dir.resolve()
    clean_service.set_authorized_root(canonical)
    return canonical


@pytest.fixture(autouse=True)
def clean_database(tmp_path: Path):
    """Provide a fresh, isolated SQLite database for each test.
    
    Mocks get_db_path to return a temporary file path, and initializes the schema.
    """
    db_path = tmp_path / "test.db"
    
    with patch("app.database.connection.get_db_path", return_value=db_path):
        # Initialize schema in the temporary database
        init_db(db_path)
        yield db_path
