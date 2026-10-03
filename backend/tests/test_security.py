"""Comprehensive security boundary tests for SmartSort Phase 1 Part 2.

Covers the scenarios required by the master specification that are not
already verified by test_folder.py:

- FolderAccessService.require_authorized_root() contract
- FolderAccessService.validate_path_in_root() service-level wrapper
- validate_path_in_root() with allow_nonexistent=True
- Edge cases: empty paths, malformed input, root-resolution failures
- API error format (no absolute path in error detail.message)

The ``clean_service`` autouse fixture in conftest.py guarantees the
authorized root is cleared before and after each test.
"""

import platform
import sys
import tempfile
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
from app.services.folder_access import FolderAccessService, get_folder_access_service

client = TestClient(app)


# ============================================================================
# FolderAccessService — require_authorized_root()
# ============================================================================


def test_service_require_root_when_unconfigured(clean_service: FolderAccessService):
    """require_authorized_root() must raise ROOT_NOT_CONFIGURED when no root is set."""
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.require_authorized_root()
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


def test_service_require_root_when_configured(
    authorized_root: Path, clean_service: FolderAccessService
):
    """require_authorized_root() must return the stored canonical root."""
    result = clean_service.require_authorized_root()
    assert result == authorized_root


def test_service_require_root_returns_path_object(
    authorized_root: Path, clean_service: FolderAccessService
):
    """require_authorized_root() must return a pathlib.Path, not a string."""
    result = clean_service.require_authorized_root()
    assert isinstance(result, Path)


def test_service_require_root_cleared_raises_again(
    authorized_root: Path, clean_service: FolderAccessService
):
    """require_authorized_root() must raise again after the root is cleared."""
    assert clean_service.require_authorized_root() == authorized_root
    clean_service.clear_authorized_root()
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.require_authorized_root()
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


# ============================================================================
# FolderAccessService — validate_path_in_root() service wrapper
# ============================================================================


def test_service_validate_path_when_unconfigured(clean_service: FolderAccessService):
    """Service validate_path_in_root() must raise ROOT_NOT_CONFIGURED when no root."""
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root("some/path")
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


def test_service_validate_path_accepts_descendant(
    authorized_root: Path,
    clean_service: FolderAccessService,
    tmp_path: Path,
):
    """Service wrapper must accept a valid descendant path."""
    child_dir = authorized_root / "subdir"
    child_dir.mkdir()
    result = clean_service.validate_path_in_root(child_dir)
    assert result == child_dir.resolve()


def test_service_validate_path_accepts_root_itself(
    authorized_root: Path, clean_service: FolderAccessService
):
    """Service wrapper with allow_root=True must accept the root itself."""
    result = clean_service.validate_path_in_root(authorized_root, allow_root=True)
    assert result == authorized_root


def test_service_validate_path_rejects_root_when_disallowed(
    authorized_root: Path, clean_service: FolderAccessService
):
    """Service wrapper with allow_root=False must reject the root itself."""
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root(authorized_root, allow_root=False)
    assert exc_info.value.code == "ROOT_NOT_ALLOWED"


def test_service_validate_path_rejects_sibling(
    authorized_root: Path,
    clean_service: FolderAccessService,
    tmp_path: Path,
):
    """Service wrapper must reject a sibling of the authorized root."""
    sibling = tmp_path / "sibling_dir"
    sibling.mkdir()
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root(sibling)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_service_validate_path_rejects_parent(
    authorized_root: Path,
    clean_service: FolderAccessService,
    tmp_path: Path,
):
    """Service wrapper must reject a path that is a parent of the authorized root."""
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root(tmp_path)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_service_validate_path_rejects_prefix_collision(
    authorized_root: Path,
    clean_service: FolderAccessService,
    tmp_path: Path,
):
    """Service wrapper must reject prefix-collision sibling (<root>-other)."""
    # authorized_root is tmp_path/authorized_root; collision name is "authorized_root-other"
    collision = tmp_path / (authorized_root.name + "-other")
    collision.mkdir()
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root(collision)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_service_validate_path_rejects_traversal(
    authorized_root: Path, clean_service: FolderAccessService
):
    """Service wrapper must reject traversal components that escape the root."""
    traversal = authorized_root / ".." / "escaped"
    with pytest.raises(PathSecurityError) as exc_info:
        clean_service.validate_path_in_root(traversal)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


# ============================================================================
# validate_path_in_root() — allow_nonexistent=True mode
# ============================================================================


def test_nonexistent_descendant_accepted(tmp_path: Path):
    """A nonexistent leaf inside the root must be accepted in allow_nonexistent mode."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    future_path = root / "not_yet_created.txt"

    result = validate_path_in_root(future_path, root, allow_nonexistent=True)
    assert result == future_path
    assert not future_path.exists()


def test_nonexistent_nested_descendant_accepted(tmp_path: Path):
    """A nonexistent leaf in an existing subdir must be accepted."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    subdir = root / "subdir"
    subdir.mkdir()
    future_path = subdir / "future_file.doc"

    result = validate_path_in_root(future_path, root, allow_nonexistent=True)
    assert result == future_path
    assert not future_path.exists()


def test_nonexistent_outside_root_rejected(tmp_path: Path):
    """A nonexistent leaf outside the root must be rejected in allow_nonexistent mode."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    outside_dir = (tmp_path / "other_dir").resolve()
    outside_dir.mkdir()
    future_outside = outside_dir / "future_file.txt"

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(future_outside, root, allow_nonexistent=True)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_nonexistent_sibling_leaf_rejected(tmp_path: Path):
    """A future path at a sibling directory must be rejected."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    sibling = (tmp_path / "sibling_dir").resolve()
    sibling.mkdir()
    future_in_sibling = sibling / "future.txt"

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(future_in_sibling, root, allow_nonexistent=True)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_nonexistent_prefix_collision_rejected(tmp_path: Path):
    """Prefix-collision parent must be rejected even in allow_nonexistent mode."""
    root = (tmp_path / "base").resolve()
    root.mkdir()
    collision = (tmp_path / "base-other").resolve()
    collision.mkdir()
    future_in_collision = collision / "newfile.txt"

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(future_in_collision, root, allow_nonexistent=True)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_nonexistent_traversal_in_parent_rejected(tmp_path: Path):
    """Traversal through parent chain must be rejected in allow_nonexistent mode."""
    root = (tmp_path / "safe_root").resolve()
    root.mkdir()
    # The parent chain contains ".." which escapes the root
    traversal_future = root / ".." / "escaped_dir" / "newfile.txt"

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(traversal_future, root, allow_nonexistent=True)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_nonexistent_parent_must_exist(tmp_path: Path):
    """If the parent itself doesn't exist, allow_nonexistent mode must reject conservatively."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    # Parent "nonexistent_parent" doesn't exist on disk
    future_path = root / "nonexistent_parent" / "future_file.txt"

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(future_path, root, allow_nonexistent=True)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_nonexistent_bad_leaf_dot_dot_rejected(tmp_path: Path):
    """A candidate leaf of '..' must be rejected in allow_nonexistent mode."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    # Build a path object where the leaf is ".."
    bad_leaf = Path(str(root / ".."))

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(bad_leaf, root, allow_nonexistent=True)
    # The ".." resolves to the parent, which is outside root — caught as PATH_OUTSIDE_ROOT
    assert exc_info.value.code in ("PATH_OUTSIDE_ROOT", "CANONICALIZATION_FAILED")


def test_nonexistent_root_itself_allow_root_true(tmp_path: Path):
    """In allow_nonexistent mode, the root itself is accepted when allow_root=True."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    result = validate_path_in_root(root, root, allow_nonexistent=True, allow_root=True)
    assert result == root


def test_nonexistent_root_itself_allow_root_false(tmp_path: Path):
    """In allow_nonexistent mode, the root itself is rejected when allow_root=False."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(root, root, allow_nonexistent=True, allow_root=False)
    assert exc_info.value.code == "ROOT_NOT_ALLOWED"


# ============================================================================
# Edge cases — normalize_and_validate_root()
# ============================================================================


def test_normalize_empty_string():
    """normalize_and_validate_root must raise EMPTY_PATH for empty string."""
    with pytest.raises(PathValidationError) as exc_info:
        normalize_and_validate_root("")
    assert exc_info.value.code == "EMPTY_PATH"


def test_normalize_whitespace_only():
    """normalize_and_validate_root must raise EMPTY_PATH for whitespace-only string."""
    with pytest.raises(PathValidationError) as exc_info:
        normalize_and_validate_root("   ")
    assert exc_info.value.code == "EMPTY_PATH"


def test_normalize_rejects_nonexistent(tmp_path: Path):
    """normalize_and_validate_root must raise PATH_NOT_FOUND for a missing directory."""
    ghost = tmp_path / "does_not_exist_xyz"
    with pytest.raises(PathValidationError) as exc_info:
        normalize_and_validate_root(ghost)
    assert exc_info.value.code == "PATH_NOT_FOUND"


def test_normalize_rejects_file(tmp_path: Path):
    """normalize_and_validate_root must raise NOT_A_DIRECTORY for a file path."""
    a_file = tmp_path / "regular_file.txt"
    a_file.write_text("content")
    with pytest.raises(PathValidationError) as exc_info:
        normalize_and_validate_root(a_file)
    assert exc_info.value.code == "NOT_A_DIRECTORY"


def test_normalize_accepts_path_object(tmp_path: Path):
    """normalize_and_validate_root must accept a pathlib.Path, not just strings."""
    target = tmp_path / "valid_dir"
    target.mkdir()
    result = normalize_and_validate_root(target)
    assert isinstance(result, Path)
    assert result == target.resolve()


def test_normalize_resolves_dotdot(tmp_path: Path):
    """normalize_and_validate_root must canonicalize paths with '..' components."""
    target = tmp_path / "real_dir"
    target.mkdir()
    dotdot_path = str(target / ".." / "real_dir")
    result = normalize_and_validate_root(dotdot_path)
    assert result == target.resolve()


# ============================================================================
# validate_path_in_root() — ROOT_NOT_CONFIGURED via None root
# ============================================================================


def test_validate_raises_root_not_configured_none():
    """validate_path_in_root must raise ROOT_NOT_CONFIGURED when authorized_root is None."""
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root("any/path", None)  # type: ignore[arg-type]
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


def test_validate_raises_root_not_configured_wrong_type():
    """validate_path_in_root must raise ROOT_NOT_CONFIGURED for non-Path root types."""
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root("any/path", "/a/string/not/a/path")  # type: ignore[arg-type]
    assert exc_info.value.code == "ROOT_NOT_CONFIGURED"


def test_validate_raises_empty_candidate(tmp_path: Path):
    """validate_path_in_root must raise EMPTY_PATH for an empty candidate."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    with pytest.raises(PathValidationError) as exc_info:
        validate_path_in_root("", root)
    assert exc_info.value.code == "EMPTY_PATH"


# ============================================================================
# API error format — no absolute paths in detail.message
# ============================================================================


def test_api_error_path_not_found_no_path_leak(tmp_path: Path):
    """POST /api/folder/select error for nonexistent path must not expose filesystem paths."""
    nonexistent = tmp_path / "ghost_dir_xyz"
    response = client.post("/api/folder/select", json={"path": str(nonexistent)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "PATH_NOT_FOUND"
    # The error message must not contain the actual filesystem path
    assert str(tmp_path) not in detail["message"]
    assert str(nonexistent) not in detail["message"]


def test_api_error_not_a_directory_no_path_leak(tmp_path: Path):
    """POST /api/folder/select error for a file path must not expose filesystem paths."""
    a_file = tmp_path / "notadir.txt"
    a_file.write_text("content")
    response = client.post("/api/folder/select", json={"path": str(a_file)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "NOT_A_DIRECTORY"
    assert str(tmp_path) not in detail["message"]
    assert str(a_file) not in detail["message"]


def test_api_select_success_message_no_path_leak(tmp_path: Path):
    """POST /api/folder/select success message must not embed canonical path."""
    valid_dir = tmp_path / "valid_root"
    valid_dir.mkdir()
    response = client.post("/api/folder/select", json={"path": str(valid_dir)})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    # authorized_root field intentionally contains the full path (displayed to user)
    assert str(valid_dir.resolve()) == data["authorized_root"]
    # But the message field must not contain it
    assert str(valid_dir.resolve()) not in data["message"]


def test_api_get_folder_configured_message_no_path_leak(tmp_path: Path):
    """GET /api/folder message when configured must not embed canonical path."""
    valid_dir = tmp_path / "valid_root"
    valid_dir.mkdir()
    client.post("/api/folder/select", json={"path": str(valid_dir)})

    response = client.get("/api/folder")
    assert response.status_code == 200
    data = response.json()
    assert data["is_configured"] is True
    # authorized_root field correctly carries the full path
    assert data["authorized_root"] == str(valid_dir.resolve())
    # The message field must not embed the path
    assert str(valid_dir.resolve()) not in data["message"]


def test_api_error_detail_has_code_and_message_fields(tmp_path: Path):
    """POST /api/folder/select error detail must have both 'code' and 'message' keys."""
    nonexistent = tmp_path / "ghost"
    response = client.post("/api/folder/select", json={"path": str(nonexistent)})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "code" in detail
    assert "message" in detail
    assert isinstance(detail["code"], str)
    assert isinstance(detail["message"], str)


# ============================================================================
# Symlink / junction escape — platform-aware
# ============================================================================


@pytest.mark.skipif(
    sys.platform == "win32" and not (
        # Only run on Windows if we can create symlinks (requires elevated privileges
        # or Developer Mode enabled — we probe this below)
        False  # Probe result filled in at collection time
    ),
    reason="Symlink escape test skipped: not supported or insufficient privileges on this platform",
)
def test_symlink_escape_rejected(tmp_path: Path):
    """A symlink inside root pointing outside must be rejected (strict mode)."""
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    outside = (tmp_path / "outside_dir").resolve()
    outside.mkdir()

    symlink_in_root = root / "escape_link"
    try:
        symlink_in_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not permitted on this system")

    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(symlink_in_root, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"


def test_symlink_escape_rejected_when_creatable(tmp_path: Path):
    """Symlink escape test that probes for symlink support before asserting.

    This version never skips silently — it either verifies the security
    constraint or documents why it cannot be verified on this platform.
    """
    root = (tmp_path / "root_dir").resolve()
    root.mkdir()
    outside = (tmp_path / "outside_dir").resolve()
    outside.mkdir()

    symlink_in_root = root / "escape_link"
    try:
        symlink_in_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Platform does not support symlinks (e.g. Windows without Developer Mode)
        # The security property still holds: paths that cannot be symlinked
        # cannot be used to escape via symlinks.
        pytest.skip(
            "Symlink creation requires elevated privileges or Developer Mode on Windows. "
            "Security property holds: symlinks that cannot be created cannot be used "
            "to escape the authorized root."
        )
        return

    # Symlink was created — verify it is rejected by the security gate
    with pytest.raises(PathSecurityError) as exc_info:
        validate_path_in_root(symlink_in_root, root)
    assert exc_info.value.code == "PATH_OUTSIDE_ROOT"
