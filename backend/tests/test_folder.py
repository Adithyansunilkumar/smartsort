"""Tests for folder selection API and path security validation.

The ``clean_service`` autouse fixture in conftest.py guarantees the authorized
root is cleared before and after every test in this module.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.paths import (
    PathSecurityError,
    PathValidationError,
    normalize_and_validate_root,
    validate_path_in_root,
)
from app.services.folder_access import get_folder_access_service

client = TestClient(app)


# ============================================================================
# API Endpoint Tests
# ============================================================================


def test_get_folder_when_unconfigured():
    """Verify GET /api/folder reports unconfigured when no root is set."""
    response = client.get("/api/folder")
    assert response.status_code == 200
    data = response.json()
    assert data["is_configured"] is False
    assert data["authorized_root"] is None
    assert "No authorized root" in data["message"]


def test_select_valid_directory(tmp_path: Path):
    """Verify POST /api/folder/select succeeds with a valid directory."""
    test_dir = tmp_path / "valid_folder"
    test_dir.mkdir()

    response = client.post("/api/folder/select", json={"path": str(test_dir)})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["authorized_root"] == str(test_dir.resolve())

    # Verify GET /api/folder reflects newly authorized root
    get_res = client.get("/api/folder")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["is_configured"] is True
    assert get_data["authorized_root"] == str(test_dir.resolve())


def test_select_nonexistent_directory(tmp_path: Path):
    """Verify POST /api/folder/select rejects non-existent paths."""
    nonexistent = tmp_path / "ghost_folder_xyz"

    response = client.post("/api/folder/select", json={"path": str(nonexistent)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "PATH_NOT_FOUND"


def test_select_file_instead_of_directory(tmp_path: Path):
    """Verify POST /api/folder/select rejects files."""
    test_file = tmp_path / "regular_file.txt"
    test_file.write_text("hello world")

    response = client.post("/api/folder/select", json={"path": str(test_file)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "NOT_A_DIRECTORY"


def test_select_empty_path():
    """Verify POST /api/folder/select rejects empty or whitespace path."""
    response = client.post("/api/folder/select", json={"path": "   "})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "EMPTY_PATH"


def test_clear_authorized_root(tmp_path: Path):
    """Verify DELETE /api/folder clears the authorized root."""
    test_dir = tmp_path / "to_clear"
    test_dir.mkdir()

    client.post("/api/folder/select", json={"path": str(test_dir)})
    assert client.get("/api/folder").json()["is_configured"] is True

    del_res = client.delete("/api/folder")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

    assert client.get("/api/folder").json()["is_configured"] is False


# ============================================================================
# Reusable Path Security & Validation Unit Tests
# ============================================================================


def test_normalize_and_validate_root_canonicalizes(tmp_path: Path):
    """Verify normalize_and_validate_root returns resolved canonical path."""
    nested = tmp_path / "sub" / ".." / "target"
    (tmp_path / "target").mkdir(parents=True)

    result = normalize_and_validate_root(nested)
    assert result == (tmp_path / "target").resolve()


def test_validate_path_in_root_nested(tmp_path: Path):
    """Verify nested file/dir inside authorized root is accepted."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    child = root / "nested" / "document.pdf"

    validated = validate_path_in_root(child, root)
    assert validated == child.resolve()


def test_validate_path_in_root_itself(tmp_path: Path):
    """Verify the root path itself is accepted when allow_root=True."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()

    validated = validate_path_in_root(root, root, allow_root=True)
    assert validated == root

    # When allow_root=False, it must be rejected
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(root, root, allow_root=False)
    assert exc_info.value.code == "ROOT_NOT_ALLOWED"


def test_validate_path_in_root_rejects_sibling(tmp_path: Path):
    """Verify sibling directory outside the authorized root is rejected."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    sibling = (tmp_path / "sibling_dir").resolve()
    sibling.mkdir()

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(sibling, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_validate_path_in_root_rejects_parent(tmp_path: Path):
    """Verify parent directory outside the authorized root is rejected."""
    root = (tmp_path / "parent_dir" / "root_dir").resolve()
    root.mkdir(parents=True)
    parent = (tmp_path / "parent_dir").resolve()

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(parent, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_validate_path_in_root_rejects_traversal(tmp_path: Path):
    """Verify path traversal (..) escaping the root is rejected."""
    root = (tmp_path / "safe_root").resolve()
    root.mkdir()

    traversal_candidate = root / ".." / "escaped.txt"
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(traversal_candidate, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_validate_path_in_root_rejects_prefix_collision(tmp_path: Path):
    """Verify prefix collision (<root>-other) is strictly rejected."""
    root = (tmp_path / "base").resolve()
    root.mkdir()
    collision = (tmp_path / "base-other").resolve()
    collision.mkdir()

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(collision, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_validate_path_in_root_unconfigured():
    """Verify validate_path_in_root raises when root is None or invalid."""
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root("some/path", None)  # type: ignore
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


def test_upload_folder():
    """Verify POST /api/folder/upload saves files and sets authorized root."""
    import io
    response = client.post(
        "/api/folder/upload",
        files=[
            ("files", ("doc1.txt", io.BytesIO(b"content 1"), "text/plain")),
            ("files", ("sub/doc2.txt", io.BytesIO(b"content 2"), "text/plain")),
        ],
        data={"relative_paths": '["my_project/doc1.txt", "my_project/sub/doc2.txt"]'},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "my_project" in data["authorized_root"]
    assert Path(data["authorized_root"]).is_dir()
    assert (Path(data["authorized_root"]) / "doc1.txt").exists()
    assert (Path(data["authorized_root"]) / "sub" / "doc2.txt").exists()


def test_upload_folder_empty():
    """Verify POST /api/folder/upload rejects empty payload."""
    response = client.post("/api/folder/upload", files=[])
    assert response.status_code == 422 or response.status_code == 400


def test_browse_folder_endpoint(monkeypatch, tmp_path):
    """Verify POST /api/folder/browse responds with expected schema."""
    monkeypatch.setattr("app.api.folder._show_native_directory_dialog", lambda: str(tmp_path))
    response = client.post("/api/folder/browse")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["path"] == str(tmp_path.resolve())


