"""Service for orchestrating document content extraction.

This module enforces Phase 1 security boundary validation before routing
supported file types to their respective parsers in `extractors.py`.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from app.database.repositories import ScanRepository, ContentRepository
from app.services.folder_access import FolderAccessService
from app.security.paths import PathSecurityError
from app.content.extractors import extract_txt, extract_pdf, extract_docx


class ExtractedDocument(BaseModel):
    """Metadata representing the result of an extraction attempt."""
    file_id: int = Field(..., description="The internal database ID of the file")
    relative_path: str = Field(..., description="Path relative to the authorized root")
    status: str = Field(..., description="SUCCESS, UNSUPPORTED, EMPTY, FAILED")
    character_count: int | None = Field(None, description="Number of characters extracted")
    page_count: int | None = Field(None, description="Number of pages (if applicable)")
    error_info: str | None = Field(None, description="Error message if extraction failed")


class ExtractionResult(BaseModel):
    """Summary of a bulk extraction operation."""
    scan_id: str = Field(..., description="The UUID of the processed scan")
    total_processed: int = Field(0, description="Total files evaluated")
    successful: int = Field(0, description="Files successfully extracted")
    empty: int = Field(0, description="Files with no extractable text")
    failed: int = Field(0, description="Files that failed to extract")
    unsupported: int = Field(0, description="Files skipped due to unsupported type")
    documents: List[ExtractedDocument] = Field(default_factory=list, description="Extraction results")


SUPPORTED_EXTENSIONS = {
    ".txt": extract_txt,
    ".pdf": extract_pdf,
    ".docx": extract_docx,
}


def extract_scan_documents(scan_id: str, folder_service: FolderAccessService) -> ExtractionResult:
    """Extract content from all supported documents in a scan.
    
    Process:
    1. Retrieve all files for the scan.
    2. Check if extraction is already successfully recorded (skip if so).
    3. Validate path via FolderAccessService.
    4. Route to parser based on extension.
    5. Save result to database.
    """
    folder_service.require_authorized_root()

    # 1. Fetch all files for this scan
    files_data = ScanRepository.get_files_by_scan_id(scan_id)
    if not files_data:
        raise ValueError(f"Scan '{scan_id}' not found or contains no files.")
        
    # 2. Fetch existing extraction results to avoid redundant work
    existing_results = ContentRepository.get_extraction_results_for_scan(scan_id)
    # Map file_id -> existing dict
    existing_map = {res["file_id"]: res for res in existing_results}

    from app.filesystem.hasher import calculate_file_hash
    from app.filesystem.scanner import _utc_iso

    result = ExtractionResult(scan_id=scan_id)
    
    for f in files_data:
        ext = f["extension"].lower()
        file_id = f["id"]
        result.total_processed += 1
        
        try:
            # 3. Security Boundary Re-Validation
            canonical_path = folder_service.validate_path_in_root(f["path"], allow_root=False)
        except PathSecurityError as err:
            status = "FAILED"
            error_info = f"Security rejection: {err.code}"
            ContentRepository.upsert_extraction_result(
                file_id=file_id, status=status, text_content=None,
                character_count=None, page_count=None,
                extracted_at=datetime.now(tz=timezone.utc).isoformat(),
                error_info=error_info, source_hash=None
            )
            result.failed += 1
            result.documents.append(ExtractedDocument(file_id=file_id, relative_path=f["path"], status=status, error_info=error_info))
            continue

        # 4. Check freshness
        current_hash = None
        try:
            stat_res = canonical_path.stat(follow_symlinks=False)
            if stat_res.st_size == f["size"] and _utc_iso(stat_res.st_mtime) == f["modified_time"] and f.get("hash"):
                current_hash = f["hash"]
            else:
                current_hash = calculate_file_hash(canonical_path)
        except OSError:
            current_hash = None

        if current_hash is None:
            # File is inaccessible/disappeared
            status = "FAILED"
            error_info = "File inaccessible or disappeared before extraction"
            ContentRepository.upsert_extraction_result(
                file_id=file_id, status=status, text_content=None,
                character_count=None, page_count=None,
                extracted_at=datetime.now(tz=timezone.utc).isoformat(),
                error_info=error_info, source_hash=None
            )
            result.failed += 1
            result.documents.append(ExtractedDocument(file_id=file_id, relative_path=f["path"], status=status, error_info=error_info))
            continue
        
        # If we already have a successful or empty extraction, check if source_hash matches
        existing = existing_map.get(file_id)
        if existing and existing.get("status") in ("SUCCESS", "EMPTY", "UNSUPPORTED"):
            if existing.get("source_hash") == current_hash:
                status = existing["status"]
                if status == "SUCCESS":
                    result.successful += 1
                elif status == "EMPTY":
                    result.empty += 1
                elif status == "UNSUPPORTED":
                    result.unsupported += 1
                    
                result.documents.append(
                    ExtractedDocument(
                        file_id=file_id,
                        relative_path=f["path"],
                        status=status,
                        character_count=existing.get("character_count"),
                        page_count=existing.get("page_count"),
                        error_info=existing.get("error_info")
                    )
                )
                continue
            
        if ext not in SUPPORTED_EXTENSIONS:
            status = "UNSUPPORTED"
            ContentRepository.upsert_extraction_result(
                file_id=file_id, status=status, text_content=None,
                character_count=None, page_count=None,
                extracted_at=datetime.now(tz=timezone.utc).isoformat(),
                error_info=None, source_hash=current_hash
            )
            result.unsupported += 1
            result.documents.append(ExtractedDocument(file_id=file_id, relative_path=f["path"], status=status))
            continue

        # 5. Route to parser
        parser = SUPPORTED_EXTENSIONS[ext]
        text_content, char_count, page_count, error_info = parser(canonical_path)
        
        if error_info:
            status = "FAILED"
            result.failed += 1
        elif text_content == "":
            status = "EMPTY"
            result.empty += 1
        else:
            status = "SUCCESS"
            result.successful += 1
            
        # 6. Save to DB
        ContentRepository.upsert_extraction_result(
            file_id=file_id, status=status, text_content=text_content,
            character_count=char_count, page_count=page_count,
            extracted_at=datetime.now(tz=timezone.utc).isoformat(),
            error_info=error_info, source_hash=current_hash
        )
        
        result.documents.append(
            ExtractedDocument(
                file_id=file_id, relative_path=f["path"], status=status,
                character_count=char_count, page_count=page_count, error_info=error_info
            )
        )

    return result
