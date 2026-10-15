"""Phase 8 — Comprehensive tests for Organization Engine.

Covers:
- Planner Rules (category grouping, renaming, warnings)
- Conflict Detection (destination exists, duplicate targets, self moves)
- Security (outside root rejection, protected path rejection)
- DB Integration / API (saving/loading plans)
- MUTATION SAFETY: Proves planning does not touch the filesystem.
"""

import os
import json
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.database.schema import init_db
from app.database.repositories import OrganizationRepository, ScanRepository, ContentRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.organization.planner import OrganizationPlanner
from app.organization.rules import normalize_filename
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService
from app.database.connection import get_connection

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()

def _make_scan(scan_id: str, files: list[ScannedFile]) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=len(files),
        directory_count=0,
        skip_count=0,
        scanned_at=_utcnow(),
        files=files,
    )

def _persist_scan(scan_id: str, files: list[ScannedFile]) -> ScanResult:
    scan = _make_scan(scan_id, files)
    ScanRepository.save_scan(scan)
    return scan

def _mock_analysis_data(scan_id: str) -> list[dict]:
    """Returns mock data resembling what AnalysisRepository returns."""
    return [
        {
            "id": 1,
            "scan_id": scan_id,
            "name": "report.pdf",
            "relative_path": "report.pdf",
            "category": "document",
            "size_bucket": "small",
            "age_bucket": "recent",
            "has_duplicates": False,
            "parent_dir": None,
            "is_root_level": True
        },
        {
            "id": 2,
            "scan_id": scan_id,
            "name": "Copy (1) of notes.txt",
            "relative_path": "Copy (1) of notes.txt",
            "category": "other",
            "size_bucket": "small",
            "age_bucket": "recent",
            "has_duplicates": False,
            "parent_dir": None,
            "is_root_level": True
        }
    ]


# ===========================================================================
# PLANNER RULES TESTS
# ===========================================================================

class TestPlannerRules:
    def test_normalize_filename(self):
        assert normalize_filename("Copy of test.txt") == ("test.txt", "Removed 'Copy of' prefix")
        assert normalize_filename("Copy (2) of notes.pdf") == ("notes.pdf", "Removed 'Copy of' prefix")
        assert normalize_filename("a    b.txt") == ("a b.txt", "Normalized whitespace")
        assert normalize_filename("clean_file.md") is None


# ===========================================================================
# PLANNER ORCHESTRATION TESTS
# ===========================================================================

class TestOrganizationPlanner:
    def test_generate_plan_success(self, authorized_root: Path):
        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(authorized_root)
        prot_svc = ProtectedPathService(folder_svc)
        planner = OrganizationPlanner(folder_svc, prot_svc)

        files_data = [
            {
                "name": "Copy of test.txt",
                "relative_path": "Copy of test.txt",
                "category": "document",
                "size_bucket": "huge",
                "age_bucket": "ancient",
                "has_duplicates": True,
                "parent_dir": None,
                "is_root_level": True,
                "file_id": 1,
                "size": 100,
            }
        ]

        plan = planner.generate_plan("s1", files_data)

        # There must be at least one CREATE_FOLDER for SmartSort_Organized and one COPY_FILE
        assert len(plan.operations) >= 2

        create_ops = [op for op in plan.operations if op.type == "CREATE_FOLDER"]
        copy_ops   = [op for op in plan.operations if op.type == "COPY_FILE"]

        assert any(op.destination == "SmartSort_Organized" for op in create_ops)
        assert len(copy_ops) == 1

        copy_op = copy_ops[0]
        assert copy_op.source == "Copy of test.txt"
        # Destination must be inside SmartSort_Organized and keep the normalized name
        assert copy_op.destination.startswith("SmartSort_Organized/")
        assert copy_op.destination.endswith("/test.txt")

        assert len(plan.warnings) >= 2
        assert any("duplicate" in w.lower() for w in plan.warnings)

    def test_conflict_destination_exists(self, authorized_root: Path):
        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(authorized_root)
        prot_svc = ProtectedPathService(folder_svc)
        planner = OrganizationPlanner(folder_svc, prot_svc)

        files_data = [
            {
                "name": "report.pdf",
                "relative_path": "report.pdf",
                "category": "document",
                "file_id": 1,
                "size": 100,
            },
            {
                "name": "report.pdf",
                "relative_path": "Documents/report.pdf",
                "category": "document",
                "file_id": 2,
                "size": 100,
            }
        ]

        # Pre-create a destination so one hit is the disk-conflict guard.
        # Determine what folder the classifier will choose (Work/Reports)
        dest_folder = "SmartSort_Organized/Work/Reports"
        conflict_dir = authorized_root / dest_folder
        conflict_dir.mkdir(parents=True, exist_ok=True)
        (conflict_dir / "report.pdf").write_bytes(b"conflict")

        plan = planner.generate_plan("s1", files_data)
        # At least one operation is skipped; warning about existing destination must appear
        assert any("already exists" in w for w in plan.warnings)
        # One file got a (2) suffix or was skipped; at most 2 COPY_FILE ops
        copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
        assert len(copy_ops) <= 2

    def test_conflict_duplicate_target(self, authorized_root: Path):
        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(authorized_root)
        prot_svc = ProtectedPathService(folder_svc)
        planner = OrganizationPlanner(folder_svc, prot_svc)

        # Both normalize to "test.txt" after stripping the "Copy of" prefix.
        # Per spec: same name, different content → both survive with (2) suffix.
        files_data = [
            {
                "name": "Copy of test.txt",
                "relative_path": "Copy of test.txt",
                "category": "other",
                "file_id": 1,
                "size": 100,
            },
            {
                "name": "Copy (1) of test.txt",
                "relative_path": "Copy (1) of test.txt",
                "category": "other",
                "file_id": 2,
                "size": 200,
            }
        ]

        plan = planner.generate_plan("s1", files_data)
        copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
        # Both files should be planned (one gets a (2) suffix)
        assert len(copy_ops) == 2
        dests = [op.destination for op in copy_ops]
        # One destination ends with /test.txt and the other with /(2).txt or test (2).txt
        assert any(d.endswith("/test.txt") for d in dests)
        assert any("(2)" in d for d in dests)

    def test_security_outside_root(self, authorized_root: Path):
        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(authorized_root)
        prot_svc = ProtectedPathService(folder_svc)
        planner = OrganizationPlanner(folder_svc, prot_svc)

        files_data = [
            {
                "name": "test.txt",
                "relative_path": "../test.txt",  # malicious
                "category": "document"
            }
        ]

        plan = planner.generate_plan("s1", files_data)
        assert len(plan.operations) == 0
        assert len(plan.protected_conflicts) == 1

    def test_security_protected_path(self, authorized_root: Path):
        folder_svc = FolderAccessService()
        folder_svc.set_authorized_root(authorized_root)
        prot_svc = ProtectedPathService(folder_svc)
        
        (authorized_root / ".git").mkdir(exist_ok=True)
        # Add protection
        target_path = ".git"
        prot_svc.add_protected_path(target_path, "system")

        planner = OrganizationPlanner(folder_svc, prot_svc)
        files_data = [
            {
                "name": "test.txt",
                "relative_path": ".git/test.txt", # source protected
                "category": "document"
            }
        ]

        plan = planner.generate_plan("s1", files_data)
        assert len(plan.operations) == 0
        assert any("protected" in c for c in plan.protected_conflicts)


# ===========================================================================
# API / REPOSITORY TESTS
# ===========================================================================

class TestOrganizationAPI:
    def test_create_plan_api(self, clean_service, authorized_root: Path):
        scan_id = "api-org"
        _persist_scan(scan_id, [
            ScannedFile(name="Copy of test.txt", relative_path="Copy of test.txt",
                        extension=".txt", size_bytes=100, modified_at="2024")
        ])
        # We need mock file_analysis rows because the planner reads from the DB
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO file_analysis (file_id, version, category, size_bytes, size_bucket, age_days, age_bucket, depth, analyzed_at, source_size, source_mtime) VALUES (1, 1, 'document', 100, 'small', 1, 'recent', 1, '2024', 100, '2024')"
            )

        resp = client.post(f"/api/organize/{scan_id}")
        assert resp.status_code == 200
        data = resp.json()["plan"]
        assert data["scan_id"] == scan_id

        # All destinations must be inside SmartSort_Organized
        for op in data["operations"]:
            assert op["destination"].startswith("SmartSort_Organized")

        # Must have at least one COPY_FILE operation
        copy_ops = [op for op in data["operations"] if op["type"] == "COPY_FILE"]
        assert len(copy_ops) >= 1
        copy_op = copy_ops[0]
        assert copy_op["source"] == "Copy of test.txt"
        # Filename is normalized ("Copy of" stripped)
        assert copy_op["destination"].endswith("test.txt")

    def test_get_plan_api(self, clean_service, authorized_root: Path):
        import uuid
        plan_id = f"plan_{uuid.uuid4().hex}"
        _persist_scan("s1", [])
        OrganizationRepository.save_plan(
            plan_id, "s1", "obj", _utcnow(), ["warn"], ["conf"], 
            [{"type": "MOVE", "source": "a.txt", "destination": "b.txt", "reason": "r", "confidence": "HIGH"}]
        )

        resp = client.get(f"/api/organize/plan/{plan_id}")
        assert resp.status_code == 200
        data = resp.json()["plan"]
        assert data["plan_id"] == plan_id
        assert data["warnings"] == ["warn"]
        assert len(data["operations"]) == 1


# ===========================================================================
# MUTATION SAFETY (CRITICAL)
# ===========================================================================

def test_mutation_safety(tmp_path: Path):
    """
    CRITICAL ACCEPTANCE CRITERION:
    Proves that organization planning performs ZERO filesystem mutations.
    """
    import os
    
    # 1. Setup real filesystem with dummy files
    f1 = tmp_path / "Copy of notes.txt"
    f1.write_text("hello")
    f1_mtime = f1.stat().st_mtime
    
    f2 = tmp_path / "report.pdf"
    f2.write_text("pdf bytes")
    f2_mtime = f2.stat().st_mtime

    # Setup database with this directory
    init_db()
    from app.services.folder_access import get_folder_access_service
    svc = get_folder_access_service()
    svc.set_authorized_root(tmp_path)
    
    scan_id = "safety_scan"
    _persist_scan(scan_id, [
        ScannedFile(name="Copy of notes.txt", relative_path="Copy of notes.txt", extension=".txt", size_bytes=5, modified_at=""),
        ScannedFile(name="report.pdf", relative_path="report.pdf", extension=".pdf", size_bytes=9, modified_at=""),
    ])
    
    with get_connection() as conn:
        conn.execute("INSERT INTO file_analysis (file_id, version, category, size_bytes, size_bucket, age_days, age_bucket, depth, analyzed_at, source_size, source_mtime) VALUES (1, 1, 'document', 5, 'small', 1, 'recent', 1, '2024', 5, '2024')")
        conn.execute("INSERT INTO file_analysis (file_id, version, category, size_bytes, size_bucket, age_days, age_bucket, depth, analyzed_at, source_size, source_mtime) VALUES (2, 1, 'document', 9, 'small', 1, 'recent', 1, '2024', 9, '2024')")

    # 2. Trigger planner
    from app.organization.service import generate_organization_plan
    plan = generate_organization_plan(scan_id, svc)

    # 3. Assert Plan proposes changes
    assert len(plan.operations) > 0
    # At least one COPY_FILE operation must be proposed
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) > 0

    # 4. Assert NO MUTATIONS occurred on the filesystem
    assert f1.exists()
    assert f1.read_text() == "hello"
    assert f1.stat().st_mtime == f1_mtime

    assert f2.exists()
    assert f2.read_text() == "pdf bytes"
    assert f2.stat().st_mtime == f2_mtime
    
    # Assert no new folders/files were created (excluding the test database)
    files = [f for f in tmp_path.iterdir() if f.suffix != ".db"]
    assert len(files) == 2
