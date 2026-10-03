"""Pydantic models for Phase 6 File Analysis."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class FileAnalysisResult(BaseModel):
    """Structured analysis signals for a single file."""
    
    file_id: int = Field(..., description="Internal DB ID of the file")
    relative_path: str = Field(..., description="Path relative to the authorized root")
    category: str = Field(..., description="Deterministic category (document, image, etc.)")
    size_bytes: int = Field(..., description="Exact size in bytes")
    size_bucket: str = Field(..., description="Size bucket (tiny, small, medium, etc.)")
    age_days: int = Field(..., description="Age in days relative to analysis time")
    age_bucket: str = Field(..., description="Age bucket (recent, old, etc.)")
    
    # Filename analysis
    name_tokens: List[str] = Field(default_factory=list, description="Tokens extracted from filename")
    is_copy: bool = Field(False, description="True if filename matches copy patterns")
    is_temp: bool = Field(False, description="True if filename matches temporary patterns")
    numeric_suffix: Optional[int] = Field(None, description="Numeric suffix if present")
    
    # Path analysis
    depth: int = Field(..., description="Directory depth relative to root")
    is_root_level: bool = Field(..., description="True if file is directly in the root")
    parent_dir: Optional[str] = Field(None, description="Name of the immediate parent directory")
    path_tokens: List[str] = Field(default_factory=list, description="Tokens from the directory path")
    
    # Duplicates
    has_duplicates: bool = Field(False, description="True if file is part of a duplicate group")
    
    # Content metrics
    char_count: Optional[int] = Field(None, description="Character count from extracted text")
    word_count: Optional[int] = Field(None, description="Word count from extracted text")
    line_count: Optional[int] = Field(None, description="Line count from extracted text")
    reading_time_mins: Optional[int] = Field(None, description="Estimated reading time in minutes")


class AnalysisSummary(BaseModel):
    """Summary of the bulk analysis operation."""
    
    scan_id: str = Field(..., description="The UUID of the processed scan")
    total_analyzed: int = Field(0, description="Total files analyzed")
    total_size_bytes: int = Field(0, description="Sum of sizes for all analyzed files")
    
    categories: Dict[str, int] = Field(default_factory=dict, description="Counts by category")
    age_buckets: Dict[str, int] = Field(default_factory=dict, description="Counts by age bucket")
    size_buckets: Dict[str, int] = Field(default_factory=dict, description="Counts by size bucket")
    
    duplicates_found: int = Field(0, description="Number of files that have duplicates")
    content_analyzed: int = Field(0, description="Number of files with content metrics")
    temp_or_copy_files: int = Field(0, description="Number of files appearing to be temp or copies")
    
    documents: List[FileAnalysisResult] = Field(default_factory=list, description="Detailed per-file results")
