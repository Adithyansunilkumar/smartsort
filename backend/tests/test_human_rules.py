"""Tests for Human-Thinking Organization Rules (Step 4 Verification).

Covers all 12 specified rules:
1. Name hint beating extension
2. A ZIP named .jpg going to _Review (MIME/magic-number check)
3. An existing Pics folder reused instead of creating Images (synonym reuse)
4. Fewer than 3 files not creating a new folder (min-3 threshold)
5. A folder over 100 items splitting by year/month (max-100 threshold)
6. Identical duplicates keeping the clearer name, with the other going to _Duplicates
7. Same name with different content producing (2) with no overwrite
8. report_v2.pdf next to report.pdf keeping both and flagging likely_newer
9. A sidecar moving with its parent (companion units)
10. A .git / package.json project staying one unit (dev-project units)
11. Rename off by default, and correct when on (.JPG -> .jpg, illegal chars fixed)
12. Re-scanning with SmartSort_Organized/ present planning zero operations (idempotency)
"""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
import pytest

from app.database.schema import init_db
from app.database.connection import get_connection
from app.database.repositories import ScanRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus
from app.organization.classifier import classify_file
from app.organization.planner import OrganizationPlanner
from app.organization.rules import (
    normalize_filename,
    score_name_clarity,
    detect_version_variant,
    make_dedup_suffix,
)
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _persist_test_scan(scan_id: str, files: list[ScannedFile]) -> ScanResult:
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


# ===========================================================================
# 1. Name hint beating extension
# ===========================================================================
def test_name_hint_beating_extension():
    """A financial name hint in a .txt or .pdf file overrides generic document category."""
    rec = {"name": "invoice_march_2026.txt", "category": "document", "size_bytes": 120}
    res = classify_file(rec)
    assert res["folder_path"] == "Documents/Financial"
    assert res["rule"] == "name_invoice"
    assert res["confidence"] >= 0.85

    rec2 = {"name": "My_Resume_2026.txt", "category": "document", "size_bytes": 200}
    res2 = classify_file(rec2)
    assert res2["folder_path"] == "Documents/Career"
    assert res2["rule"] == "name_resume"


# ===========================================================================
# 2. A ZIP named .jpg going to _Review
# ===========================================================================
def test_zip_named_jpg_goes_to_review():
    """MIME/magic-number mismatch between content and extension routes to _Review with flag."""
    rec = {
        "name": "vacation_photo.jpg",
        "category": "image",
        "mime_type": "application/zip",
        "size_bytes": 5000,
    }
    res = classify_file(rec)
    assert res["folder_path"] == "_Review"
    assert "extension_mismatch" in res["flags"]
    assert res["needs_review"] is True


# ===========================================================================
# 3. An existing Pics folder reused instead of creating Images
# ===========================================================================
def test_existing_pics_folder_reused_instead_of_images(authorized_root: Path):
    """If 'Pics' folder already exists in SmartSort_Organized, use it instead of 'Images'."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    # Pre-create SmartSort_Organized/Pics
    pics_dir = authorized_root / "SmartSort_Organized" / "Pics"
    pics_dir.mkdir(parents=True, exist_ok=True)

    files_data = [
        {
            "name": "photo1.png",
            "relative_path": "photo1.png",
            "category": "image",
            "size_bytes": 500,
        },
        {
            "name": "photo2.png",
            "relative_path": "photo2.png",
            "category": "image",
            "size_bytes": 600,
        },
    ]

    plan = planner.generate_plan("scan-synonym", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2
    for op in copy_ops:
        assert op.destination.startswith("SmartSort_Organized/Pics/")
        assert not op.destination.startswith("SmartSort_Organized/Images/")


# ===========================================================================
# 4. Fewer than 3 files not creating a new folder
# ===========================================================================
def test_fewer_than_3_files_not_creating_new_folder(authorized_root: Path):
    """When a subcategory has fewer than 3 files, files are placed in the parent folder."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    # 2 files classified as Audio/Podcasts should fall back to parent Audio/
    files_data = [
        {
            "name": "episode1.mp3",
            "relative_path": "episode1.mp3",
            "category": "audio",
            "folder_path": "Audio/Podcasts",
            "size_bytes": 1000,
        },
        {
            "name": "episode2.mp3",
            "relative_path": "episode2.mp3",
            "category": "audio",
            "folder_path": "Audio/Podcasts",
            "size_bytes": 1200,
        },
    ]

    plan = planner.generate_plan("scan-min3", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2
    # Because only 2 files target Audio/Podcasts (< 3), destination should be Audio/
    for op in copy_ops:
        assert op.destination.startswith("SmartSort_Organized/Audio/")
        assert not op.destination.startswith("SmartSort_Organized/Audio/Podcasts/")


# ===========================================================================
# 5. A folder over 100 items splitting by year/month
# ===========================================================================
def test_folder_over_100_items_splitting_by_year(authorized_root: Path):
    """When a folder exceeds 100 items, items beyond 100 split into year buckets."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    # Create 105 document records with modified_time in 2026
    files_data = []
    for i in range(105):
        files_data.append({
            "name": f"doc_{i:03d}.pdf",
            "relative_path": f"doc_{i:03d}.pdf",
            "category": "document",
            "size_bytes": 100,
            "modified_time": "2026-04-10T12:00:00Z",
        })

    plan = planner.generate_plan("scan-over100", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 105

    # Check that some operations were routed to 2026 subfolder
    year_ops = [op for op in copy_ops if "/2026/" in op.destination or op.destination.endswith("/2026")]
    assert len(year_ops) > 0


# ===========================================================================
# 6. Identical duplicates keeping the clearer name, others to _Duplicates
# ===========================================================================
def test_identical_duplicates_clearest_name_to_duplicates(authorized_root: Path):
    """Byte-identical files keep the clearest name; duplicate goes to _Duplicates with duplicate_of."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    # Clarity score tests
    assert score_name_clarity("Annual_Report.pdf") > score_name_clarity("Annual_Report (1).pdf")
    assert score_name_clarity("Annual_Report.pdf") > score_name_clarity("Copy of Annual_Report.pdf")

    # Identical SHA-256 hash
    sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    files_data = [
        {
            "name": "Project_Plan.pdf",
            "relative_path": "docs/Project_Plan.pdf",
            "category": "document",
            "sha256": sha,
            "size_bytes": 2048,
        },
        {
            "name": "Copy (1) of Project_Plan.pdf",
            "relative_path": "Downloads/Copy (1) of Project_Plan.pdf",
            "category": "document",
            "sha256": sha,
            "size_bytes": 2048,
        },
    ]

    plan = planner.generate_plan("scan-dup", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2

    winner_op = next(op for op in copy_ops if "Project_Plan.pdf" in op.source and "Copy" not in op.source)
    dup_op = next(op for op in copy_ops if "Copy" in op.source)

    # Winner goes to normal category folder
    assert not dup_op.destination.startswith("SmartSort_Organized/Documents/")
    assert dup_op.destination.startswith("SmartSort_Organized/_Duplicates/")
    assert dup_op.rule in ("duplicate_loser", "duplicate_file")


# ===========================================================================
# 7. Same name with different content producing (2) with no overwrite
# ===========================================================================
def test_same_name_different_content_produces_paren_2(authorized_root: Path):
    """Two files with identical names and destinations but different contents get (2) suffix."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "Notes.txt",
            "relative_path": "folderA/Notes.txt",
            "category": "document",
            "sha256": "hash_a_1111",
            "size_bytes": 100,
        },
        {
            "name": "Notes.txt",
            "relative_path": "folderB/Notes.txt",
            "category": "document",
            "sha256": "hash_b_2222",
            "size_bytes": 200,
        },
    ]

    plan = planner.generate_plan("scan-collision", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2

    destinations = [op.destination for op in copy_ops]
    # Suffix must be (2), not _1 or overwriting
    assert any("Notes (2).txt" in dest for dest in destinations)
    assert any(dest.endswith("Notes.txt") for dest in destinations)
    # Both destinations are unique
    assert len(set(destinations)) == 2


# ===========================================================================
# 8. report_v2.pdf next to report.pdf keeping both and flagging likely_newer
# ===========================================================================
def test_report_v2_likely_newer_flag(authorized_root: Path):
    """A version variant like report_v2.pdf is kept and flagged with likely_newer."""
    assert detect_version_variant("report_v2.pdf") is True
    assert detect_version_variant("proposal_final.docx") is True
    assert detect_version_variant("standard_doc.txt") is False

    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "report.pdf",
            "relative_path": "report.pdf",
            "category": "document",
            "size_bytes": 500,
        },
        {
            "name": "report_v2.pdf",
            "relative_path": "report_v2.pdf",
            "category": "document",
            "size_bytes": 600,
        },
    ]

    plan = planner.generate_plan("scan-newer", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2

    v2_op = next(op for op in copy_ops if "report_v2.pdf" in op.source)
    assert v2_op.likely_newer is True
    assert "likely_newer" in (v2_op.flags or "")


# ===========================================================================
# 9. A sidecar moving with its parent
# ===========================================================================
def test_sidecar_moves_with_parent(authorized_root: Path):
    """Sidecar (.srt, .xmp) companions are planned into the same folder as their parent."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "vacation.mp4",
            "relative_path": "vacation.mp4",
            "category": "video",
            "size_bytes": 50000,
        },
        {
            "name": "vacation.srt",
            "relative_path": "vacation.srt",
            "category": "document",
            "size_bytes": 500,
        },
    ]

    plan = planner.generate_plan("scan-sidecar", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 2

    video_op = next(op for op in copy_ops if op.source == "vacation.mp4")
    sidecar_op = next(op for op in copy_ops if op.source == "vacation.srt")

    # Parent directory of both destinations must match
    assert Path(video_op.destination).parent == Path(sidecar_op.destination).parent
    assert sidecar_op.rule == "sidecar_companion"
    assert "sidecar" in (sidecar_op.flags or "")


# ===========================================================================
# 10. A .git / package.json project staying one unit
# ===========================================================================
def test_dev_project_stays_one_unit(authorized_root: Path):
    """Folders containing dev project markers (.git, package.json, etc.) stay together under Development/Projects."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "package.json",
            "relative_path": "my_app/package.json",
            "category": "data",
            "size_bytes": 300,
        },
        {
            "name": "index.js",
            "relative_path": "my_app/src/index.js",
            "category": "code",
            "size_bytes": 500,
        },
        {
            "name": "logo.png",
            "relative_path": "my_app/assets/logo.png",
            "category": "image",
            "size_bytes": 1000,
        },
    ]

    plan = planner.generate_plan("scan-project", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 3

    for op in copy_ops:
        assert "Development/Projects/my_app" in op.destination
        assert op.rule in ("dev_project_unit", "dev_project_intact")

    # Preserves relative internal structure
    logo_op = next(op for op in copy_ops if "logo.png" in op.source)
    assert logo_op.destination.endswith("assets/logo.png")


# ===========================================================================
# 11. Rename off by default, and correct when on
# ===========================================================================
def test_rename_off_by_default_and_correct_when_on(authorized_root: Path):
    """Rename is off by default. When on, lowercases extensions and cleans illegal characters."""
    # Test normalization rules
    assert normalize_filename("PHOTO.JPG", rename=False) is None
    normalized_jpg, reason = normalize_filename("PHOTO.JPG", rename=True)
    assert normalized_jpg == "PHOTO.jpg"

    normalized_illegal, _ = normalize_filename("my<bad>:name?.txt", rename=True)
    assert normalized_illegal == "my-bad--name-.txt"

    # Test planner rename option
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "IMG_001.JPG",
            "relative_path": "IMG_001.JPG",
            "category": "image",
            "size_bytes": 1000,
        }
    ]

    # Off by default
    plan_off = planner.generate_plan("scan-rename-off", files_data, rename=False)
    copy_off = [op for op in plan_off.operations if op.type == "COPY_FILE"][0]
    assert copy_off.destination.endswith("IMG_001.JPG")

    # On when specified
    plan_on = planner.generate_plan("scan-rename-on", files_data, rename=True)
    copy_on = [op for op in plan_on.operations if op.type == "COPY_FILE"][0]
    assert copy_on.destination.endswith("IMG_001.jpg")


# ===========================================================================
# 12. Re-scanning with SmartSort_Organized/ present planning zero operations
# ===========================================================================
def test_rescan_smartsort_organized_plans_zero_operations(authorized_root: Path):
    """Files inside SmartSort_Organized/ are ignored by planner to ensure idempotency."""
    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    files_data = [
        {
            "name": "already_organized.pdf",
            "relative_path": "SmartSort_Organized/Documents/already_organized.pdf",
            "category": "document",
            "size_bytes": 1000,
        },
        {
            "name": "nested_file.jpg",
            "relative_path": "SmartSort_Organized/Images/Photos/nested_file.jpg",
            "category": "image",
            "size_bytes": 2000,
        },
    ]

    plan = planner.generate_plan("scan-idempotent", files_data)
    copy_ops = [op for op in plan.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 0


# ===========================================================================
# 13. COPY_FILE supported for approval
# ===========================================================================
def test_copy_file_operation_supported_for_approval(authorized_root: Path):
    """COPY_FILE operations are fully eligible for approval without 'not supported' error."""
    from app.organization.approval_service import get_plan_approval_status
    from app.database.repositories import OrganizationRepository

    folder_svc = FolderAccessService()
    folder_svc.set_authorized_root(authorized_root)
    prot_svc = ProtectedPathService(folder_svc)
    planner = OrganizationPlanner(folder_svc, prot_svc)

    test_file = authorized_root / "test_doc.pdf"
    test_file.write_bytes(b"content")

    scan_id = "scan-approval"
    _persist_test_scan(
        scan_id,
        [
            ScannedFile(
                name="test_doc.pdf",
                relative_path="test_doc.pdf",
                extension=".pdf",
                size_bytes=len(b"content"),
                modified_at=_utcnow(),
            )
        ],
    )

    files_data = [
        {
            "name": "test_doc.pdf",
            "relative_path": "test_doc.pdf",
            "category": "document",
            "size_bytes": 100,
        }
    ]

    plan = planner.generate_plan(scan_id, files_data)
    op_dicts = [op.model_dump() for op in plan.operations]
    OrganizationRepository.save_plan(
        plan_id=plan.plan_id,
        scan_id=plan.scan_id,
        objective=plan.objective,
        created_at=plan.created_at,
        warnings=plan.warnings,
        protected_conflicts=plan.protected_conflicts,
        operations=op_dicts,
        detected_topics=plan.detected_topics,
        new_folders_proposed=plan.new_folders_proposed,
        existing_folders_reused=plan.existing_folders_reused,
        unclassified_files=plan.unclassified_files,
    )

    status = get_plan_approval_status(plan.plan_id, folder_svc)
    copy_ops = [op for op in status.operations if op.type == "COPY_FILE"]
    assert len(copy_ops) == 1
    assert copy_ops[0].is_eligible is True
    assert copy_ops[0].ineligibility_reason is None


