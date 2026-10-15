"""End-to-end integration test verifying the complete workflow:
Select Folder → Scan → Analyze → Generate Plan → Approve → Execute → Verify.

Tests against representative dataset:
- PDF documents
- TXT and Markdown files
- DOCX documents
- Images (JPG, PNG)
- Nested directories
- Files with duplicate names in different directories
- Unsupported file types
- Empty directories
"""

import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from app.services.folder_access import get_folder_access_service

client = TestClient(app)


def test_complete_e2e_workflow():
    """Verify the full 14 criteria of the problem description."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()

        # 1. Populate representative test dataset
        (root / "contract.pdf").write_bytes(b"%PDF-1.4 mock pdf content")
        (root / "meeting_notes.txt").write_text("Notes from meeting")
        (root / "guide.md").write_text("# Project Guide\nMarkdown content")
        (root / "proposal.docx").write_bytes(b"PK\x03\x04 mock docx binary")
        (root / "banner.png").write_bytes(b"\x89PNG\r\n\x1a\n mock png image")
        (root / "avatar.jpg").write_bytes(b"\xff\xd8\xff mock jpg image")

        # Nested directory with files
        nested_dir = root / "subfolder"
        nested_dir.mkdir()
        (nested_dir / "nested_doc.txt").write_text("Nested document content")

        # Duplicate filename in different directory
        (nested_dir / "banner.png").write_bytes(b"\x89PNG\r\n\x1a\n duplicate name banner")

        # Unsupported file type
        (root / "binary.unsupported_ext_999").write_bytes(b"\x00\x01\x02\x03 unknown")

        # Empty directory
        empty_dir = root / "empty_dir"
        empty_dir.mkdir()

        # ---------------------------------------------------------------------
        # Step 1: Select Folder
        # ---------------------------------------------------------------------
        resp_select = client.post("/api/folder/select", json={"path": str(root)})
        assert resp_select.status_code == 200, resp_select.text
        select_data = resp_select.json()
        assert select_data["status"] == "success"

        # ---------------------------------------------------------------------
        # Step 2: Scan Files
        # ---------------------------------------------------------------------
        resp_scan = client.post("/api/scan")
        assert resp_scan.status_code == 200, resp_scan.text
        scan_data = resp_scan.json()
        scan_id = scan_data["scan_id"]
        assert scan_data["status"] == "completed"
        # 8 files total: contract.pdf, meeting_notes.txt, guide.md, proposal.docx,
        # banner.png, avatar.jpg, subfolder/nested_doc.txt, subfolder/banner.png, binary.unsupported_ext_999
        assert scan_data["file_count"] == 9
        assert scan_data["directory_count"] >= 2  # subfolder, empty_dir

        # Persisted and retrievable
        files_found = {f["name"] for f in scan_data["files"]}
        assert "contract.pdf" in files_found
        assert "binary.unsupported_ext_999" in files_found

        # ---------------------------------------------------------------------
        # Step 3: Analyze
        # ---------------------------------------------------------------------
        resp_analyze = client.post(f"/api/analyze/{scan_id}")
        assert resp_analyze.status_code == 200, resp_analyze.text
        analysis = resp_analyze.json()
        assert analysis["scan_id"] == scan_id
        assert analysis["total_analyzed"] == 9
        assert analysis["total_size_bytes"] > 0
        assert "document" in analysis["categories"]
        assert "image" in analysis["categories"]
        assert "unknown" in analysis["categories"]  # unsupported file

        # Test GET /api/analyze/{scan_id}
        resp_analyze_get = client.get(f"/api/analyze/{scan_id}")
        assert resp_analyze_get.status_code == 200
        assert resp_analyze_get.json()["total_analyzed"] == 9

        # ---------------------------------------------------------------------
        # Step 4: Plan Generation
        # ---------------------------------------------------------------------
        # Record mtimes and files before planning to verify mutation safety
        files_before = {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}

        resp_plan = client.post(f"/api/organize/{scan_id}")
        assert resp_plan.status_code == 200, resp_plan.text
        plan_data = resp_plan.json()["plan"]
        plan_id = plan_data["plan_id"]
        assert plan_data["scan_id"] == scan_id
        assert len(plan_data["operations"]) > 0

        # Verify mutation safety: planning must NOT touch filesystem
        files_after_plan = {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}
        assert files_before == files_after_plan, "Filesystem was mutated during plan generation!"

        # Verify proposed source and destinations
        for op in plan_data["operations"]:
            assert op["destination"].startswith("SmartSort_Organized")
            if op["type"] in ("MOVE", "RENAME"):
                # Source must be relative path that exists
                assert (root / op["source"]).exists()

        # ---------------------------------------------------------------------
        # Step 5: Approval Workflow
        # ---------------------------------------------------------------------
        resp_approvals = client.get(f"/api/plans/{plan_id}/approvals")
        assert resp_approvals.status_code == 200
        approvals = resp_approvals.json()
        assert approvals["total_operations"] == len(plan_data["operations"])

        # Bulk approve
        resp_bulk_app = client.post(f"/api/plans/{plan_id}/approvals/bulk-approve")
        assert resp_bulk_app.status_code == 200, resp_bulk_app.text
        bulk_data = resp_bulk_app.json()
        assert bulk_data["updated_count"] > 0
        assert bulk_data["plan_status"]["approved_count"] == bulk_data["updated_count"]

        # ---------------------------------------------------------------------
        # Step 6: Execution
        # ---------------------------------------------------------------------
        resp_exec = client.post(f"/api/plans/{plan_id}/execute", json={})
        assert resp_exec.status_code == 200
        exec_data = resp_exec.json()
        assert exec_data["completed_count"] > 0
        assert exec_data["failed_count"] == 0

        # Verify originals remain untouched
        for orig in files_before:
            assert (root / orig).exists(), f"Original file {orig} was deleted or moved!"

        # Verify organized files exist inside SmartSort_Organized
        org_folder = root / "SmartSort_Organized"
        assert org_folder.is_dir()
        # Content-aware classifier places files into specific topic folders or
        # broad category fallbacks. Verify that at least one subfolder was created.
        subfolders = [p for p in org_folder.rglob("*") if p.is_dir()]
        assert len(subfolders) >= 1, f"Expected at least one subfolder in SmartSort_Organized, got: {subfolders}"

        # ---------------------------------------------------------------------
        # Step 7: Verification
        # ---------------------------------------------------------------------
        resp_verify = client.post(f"/api/plans/{plan_id}/verify")
        assert resp_verify.status_code == 200
        verify_data = resp_verify.json()
        assert len(verify_data["verifications"]) == len(plan_data["operations"])


def test_empty_directory_workflow():
    """Verify that an empty directory produces proper empty states and messages."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()

        # Select
        resp_select = client.post("/api/folder/select", json={"path": str(root)})
        assert resp_select.status_code == 200

        # Scan
        resp_scan = client.post("/api/scan")
        assert resp_scan.status_code == 200
        scan_id = resp_scan.json()["scan_id"]
        assert resp_scan.json()["file_count"] == 0

        # Analyze
        resp_analyze = client.post(f"/api/analyze/{scan_id}")
        assert resp_analyze.status_code == 200
        assert resp_analyze.json()["total_analyzed"] == 0

        # Plan
        resp_plan = client.post(f"/api/organize/{scan_id}")
        assert resp_plan.status_code == 200
        plan = resp_plan.json()["plan"]
        assert len(plan["operations"]) == 0
        assert any("No files" in w for w in plan["warnings"])
