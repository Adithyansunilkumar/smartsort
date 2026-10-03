"""Service for orchestrating deterministic file analysis (Phase 6)."""

import json
from datetime import datetime, timezone
from typing import Dict, Any, List

from app.database.repositories import ScanRepository, ContentRepository, AnalysisRepository
from app.database.connection import get_connection
from app.services.folder_access import FolderAccessService
from app.filesystem.hasher import calculate_file_hash
from app.security.paths import PathSecurityError
from app.analysis.models import AnalysisSummary, FileAnalysisResult
from app.analysis.rules import (
    ANALYSIS_VERSION,
    get_file_category,
    get_age_metrics,
    get_size_bucket,
    parse_filename,
    analyze_path,
    analyze_content,
)


def analyze_scan_files(scan_id: str, folder_service: FolderAccessService) -> AnalysisSummary:
    """Analyze files deterministically, checking freshness against cached analysis."""
    
    folder_service.require_authorized_root()
    
    files_data = ScanRepository.get_files_by_scan_id(scan_id)
    if not files_data:
        raise ValueError(f"Scan '{scan_id}' not found or contains no files.")
        
    # Get existing content extractions and analysis records
    content_extractions = ContentRepository.get_extraction_results_for_scan(scan_id)
    content_map = {c["file_id"]: c for c in content_extractions if c["status"] in ("SUCCESS", "EMPTY")}
    
    existing_analyses = AnalysisRepository.get_analysis_results_for_scan(scan_id)
    analysis_map = {a["file_id"]: a for a in existing_analyses}
    
    # We need to know about duplicates (Phase 4)
    # We can fetch duplicates from the DB to see if a file has duplicates.
    # Group by hash where count > 1
    duplicate_hashes = set()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT hash FROM files 
            WHERE scan_id = ? AND hash IS NOT NULL 
            GROUP BY hash HAVING COUNT(*) > 1
            """,
            (scan_id,)
        )
        for row in cursor:
            duplicate_hashes.add(row["hash"])
            
    summary = AnalysisSummary(scan_id=scan_id)
    analysis_time_iso = datetime.now(tz=timezone.utc).isoformat()
    
    for f in files_data:
        file_id = f["id"]
        rel_path = f["path"]
        
        # 1. Security boundary check
        try:
            canonical_path = folder_service.validate_path_in_root(rel_path, allow_root=False)
        except PathSecurityError:
            continue  # Skip files that fail security boundary
            
        # 2. Check current file state
        try:
            stat_res = canonical_path.stat(follow_symlinks=False)
            current_size = stat_res.st_size
            current_mtime = str(stat_res.st_mtime)
        except OSError:
            continue  # File disappeared or inaccessible
            
        # 3. Retrieve relevant metadata
        existing_analysis = analysis_map.get(file_id)
        content_info = content_map.get(file_id)
        
        current_hash = f.get("hash")  # We prefer persisted hash from Phase 4
        # We don't strictly re-hash if size and mtime are same as analysis cached ones
        # But if we need it for duplicate checks and it's missing, it's ok, Phase 4 should handle that.
        
        content_hash = content_info.get("source_hash") if content_info else None
        has_dupes = current_hash in duplicate_hashes if current_hash else False
        
        # 4. Freshness check
        is_stale = True
        if existing_analysis and existing_analysis["version"] == ANALYSIS_VERSION:
            if existing_analysis["source_size"] == current_size and \
               existing_analysis["source_mtime"] == current_mtime:
                # Basic metadata hasn't changed. Check content hash.
                if existing_analysis.get("source_content_hash") == content_hash:
                    # Not stale!
                    is_stale = False
                    
        if is_stale:
            # Recompute analysis deterministically
            category = get_file_category(f["extension"], f.get("mime_type"))
            # For age, we use analysis_time_iso to freeze "now" for determinism in the batch
            age_days, age_bucket = get_age_metrics(f["modified_time"], analysis_time_iso)
            size_bucket = get_size_bucket(f["size"])
            
            name_tokens, numeric_suffix, is_copy, is_temp = parse_filename(f["name"], f["extension"])
            depth, is_root_level, parent_dir, path_tokens = analyze_path(f["path"])
            
            text_content = None
            if content_info:
                # Fetch text_content on demand to save memory
                with get_connection() as conn:
                    cur = conn.execute("SELECT text_content FROM document_content WHERE file_id = ?", (file_id,))
                    row = cur.fetchone()
                    if row:
                        text_content = row["text_content"]
                        
            char_count, word_count, line_count, reading_time_mins = analyze_content(text_content)
            
            # Save to DB
            analysis_data = {
                "file_id": file_id,
                "version": ANALYSIS_VERSION,
                "category": category,
                "size_bytes": f["size"],
                "size_bucket": size_bucket,
                "age_days": age_days,
                "age_bucket": age_bucket,
                "name_tokens": json.dumps(name_tokens),
                "is_copy": is_copy,
                "is_temp": is_temp,
                "numeric_suffix": numeric_suffix,
                "depth": depth,
                "is_root_level": is_root_level,
                "parent_dir": parent_dir,
                "path_tokens": json.dumps(path_tokens),
                "has_duplicates": has_dupes,
                "char_count": char_count,
                "word_count": word_count,
                "line_count": line_count,
                "reading_time_mins": reading_time_mins,
                "analyzed_at": analysis_time_iso,
                "source_size": current_size,
                "source_mtime": current_mtime,
                "source_hash": current_hash,
                "source_content_hash": content_hash
            }
            AnalysisRepository.upsert_file_analysis(analysis_data)
            
            # Populate Pydantic model for response
            res_model = FileAnalysisResult(
                file_id=file_id,
                relative_path=rel_path,
                category=category,
                size_bytes=f["size"],
                size_bucket=size_bucket,
                age_days=age_days,
                age_bucket=age_bucket,
                name_tokens=name_tokens,
                is_copy=is_copy,
                is_temp=is_temp,
                numeric_suffix=numeric_suffix,
                depth=depth,
                is_root_level=is_root_level,
                parent_dir=parent_dir,
                path_tokens=path_tokens,
                has_duplicates=has_dupes,
                char_count=char_count,
                word_count=word_count,
                line_count=line_count,
                reading_time_mins=reading_time_mins
            )
        else:
            # Load from existing
            res_model = FileAnalysisResult(
                file_id=file_id,
                relative_path=rel_path,
                category=existing_analysis["category"],
                size_bytes=existing_analysis["size_bytes"],
                size_bucket=existing_analysis["size_bucket"],
                age_days=existing_analysis["age_days"],
                age_bucket=existing_analysis["age_bucket"],
                name_tokens=json.loads(existing_analysis["name_tokens"]) if existing_analysis.get("name_tokens") else [],
                is_copy=bool(existing_analysis["is_copy"]),
                is_temp=bool(existing_analysis["is_temp"]),
                numeric_suffix=existing_analysis.get("numeric_suffix"),
                depth=existing_analysis["depth"],
                is_root_level=bool(existing_analysis["is_root_level"]),
                parent_dir=existing_analysis.get("parent_dir"),
                path_tokens=json.loads(existing_analysis["path_tokens"]) if existing_analysis.get("path_tokens") else [],
                has_duplicates=bool(existing_analysis["has_duplicates"]),
                char_count=existing_analysis.get("char_count"),
                word_count=existing_analysis.get("word_count"),
                line_count=existing_analysis.get("line_count"),
                reading_time_mins=existing_analysis.get("reading_time_mins")
            )
            
        # Update summary
        summary.total_analyzed += 1
        summary.total_size_bytes += res_model.size_bytes
        
        summary.categories[res_model.category] = summary.categories.get(res_model.category, 0) + 1
        summary.age_buckets[res_model.age_bucket] = summary.age_buckets.get(res_model.age_bucket, 0) + 1
        summary.size_buckets[res_model.size_bucket] = summary.size_buckets.get(res_model.size_bucket, 0) + 1
        
        if res_model.has_duplicates:
            summary.duplicates_found += 1
        if res_model.word_count is not None:
            summary.content_analyzed += 1
        if res_model.is_copy or res_model.is_temp:
            summary.temp_or_copy_files += 1
            
        summary.documents.append(res_model)
        
    return summary
