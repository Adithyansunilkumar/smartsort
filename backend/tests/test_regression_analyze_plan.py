"""Regression tests for Analyze and Plan phases.

Verifies:
1. Empty scan can be analyzed successfully without 404, returning total_analyzed=0.
2. Empty scan can generate an organization plan without 404, returning empty operations and warnings.
3. Non-existent scan returns 404 SCAN_NOT_FOUND.
4. Unsupported file types are analyzed gracefully without crashing.
5. GET and POST /api/analyze/{scan_id} endpoints return valid analysis summaries.
6. Organization plan correctly maps files to SmartSort_Organized destinations.
"""

from pathlib import Path
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.database.repositories import ScanRepository
from app.services.folder_access import FolderAccessService

client = TestClient(app)


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _persist_scan(scan_id: str, files: list[ScannedFile]) -> ScanResult:
    scan = ScanResult(
        scan_id=scan_id,
        status=ScanStatus.COMPLETED,
        root_display="TestRoot",
        file_count=len(files),
        directory_count=0,
        skip_count=0,
        scanned_at=_utcnow(),
        files=files,
    )
    ScanRepository.save_scan(scan)
    return scan


class TestAnalyzePlanRegression:
    def test_nonexistent_scan_returns_404(self, clean_service: FolderAccessService, authorized_root: Path):
        resp = client.post("/api/analyze/nonexistent-scan-id")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "SCAN_NOT_FOUND"

        resp_plan = client.post("/api/organize/nonexistent-scan-id")
        assert resp_plan.status_code == 404
        assert resp_plan.json()["detail"]["code"] == "SCAN_NOT_FOUND"

    def test_empty_scan_analyze_succeeds(self, clean_service: FolderAccessService, authorized_root: Path):
        scan_id = "empty-scan-reg"
        _persist_scan(scan_id, [])

        # POST /api/analyze/{scan_id}
        resp = client.post(f"/api/analyze/{scan_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == scan_id
        assert data["total_analyzed"] == 0
        assert data["total_size_bytes"] == 0
        assert len(data["documents"]) == 0

        # GET /api/analyze/{scan_id}
        resp_get = client.get(f"/api/analyze/{scan_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["total_analyzed"] == 0

    def test_empty_scan_plan_succeeds(self, clean_service: FolderAccessService, authorized_root: Path):
        scan_id = "empty-scan-plan-reg"
        _persist_scan(scan_id, [])

        resp = client.post(f"/api/organize/{scan_id}")
        assert resp.status_code == 200
        plan = resp.json()["plan"]
        assert plan["scan_id"] == scan_id
        assert len(plan["operations"]) == 0
        assert any("No files" in w for w in plan["warnings"])

    def test_unsupported_file_types_analyze_and_plan(self, clean_service: FolderAccessService, authorized_root: Path):
        # Create physical files for stat checks
        unsupported_file = authorized_root / "data.xyz123"
        unsupported_file.write_bytes(b"arbitrary binary content")

        doc_file = authorized_root / "notes.txt"
        doc_file.write_text("plain text notes")

        scan_id = "mixed-files-reg"
        _persist_scan(scan_id, [
            ScannedFile(
                name="data.xyz123",
                relative_path="data.xyz123",
                extension=".xyz123",
                size_bytes=unsupported_file.stat().st_size,
                modified_at=_utcnow(),
                mime_type=None,
            ),
            ScannedFile(
                name="notes.txt",
                relative_path="notes.txt",
                extension=".txt",
                size_bytes=doc_file.stat().st_size,
                modified_at=_utcnow(),
                mime_type="text/plain",
            ),
        ])

        # Analyze
        resp_analyze = client.post(f"/api/analyze/{scan_id}")
        assert resp_analyze.status_code == 200
        analysis_data = resp_analyze.json()
        assert analysis_data["total_analyzed"] == 2
        assert "unknown" in analysis_data["categories"]
        assert "document" in analysis_data["categories"] or "plain text" in analysis_data["categories"]

        # Plan
        resp_plan = client.post(f"/api/organize/{scan_id}")
        assert resp_plan.status_code == 200
        plan = resp_plan.json()["plan"]
        assert len(plan["operations"]) > 0
        # All destination paths must be within SmartSort_Organized
        for op in plan["operations"]:
            assert op["destination"].startswith("SmartSort_Organized")
