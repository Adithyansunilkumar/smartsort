"""Content-aware, deterministic file classification engine for SmartSort.

This module analyses filename tokens, file extensions, and extracted text to
produce a specific, meaningful folder path and a confidence score for each
file.  It does NOT use any LLM.  All rules are keyword-based and
deterministic.

Folder depth is limited to MAX_DEPTH levels below the SmartSort_Organized
root (default 3).  Fallback categories mirror the broad extension-based
categories already used by the analysis phase.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_DEPTH: int = 3  # Maximum folder levels below SmartSort_Organized

# ---------------------------------------------------------------------------
# Stop-words – tokens that carry no topical meaning
# ---------------------------------------------------------------------------

STOP_WORDS: frozenset = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
        "by", "is", "it", "its", "be", "as", "are", "was", "were", "from",
        "with", "this", "that", "these", "those", "my", "our", "your", "has",
        "have", "had", "not", "but", "do", "did", "does", "will", "would",
        "can", "could", "should", "about", "into", "than", "then", "so",
        "we", "they", "he", "she", "you", "me", "him", "her", "us",
        "new", "old", "all", "also", "any", "each", "file", "files", "doc",
        "document", "docs", "image", "images", "photo", "video", "copy",
        "final", "draft", "version", "ver", "rev", "update", "updated",
        "temp", "tmp", "test", "backup", "bak", "misc", "other", "stuff",
        "sample", "example", "untitled", "na",
        # Generic single-word tokens that are too broad to classify on their own
        "data", "science", "content", "info", "information",
    }
)

# ---------------------------------------------------------------------------
# Semantic pattern tables
# Each entry: (keywords_frozenset, folder_path_components, priority)
# Keywords are matched against the combined token set (filename + content).
# Higher priority wins when multiple patterns match.
# ---------------------------------------------------------------------------

SEMANTIC_PATTERNS: List[Tuple[frozenset, List[str], int]] = [
    # ── Finance ──────────────────────────────────────────────────────────
    (frozenset({"invoice", "invoices"}),                    ["Finance", "Invoices"],                  90),
    (frozenset({"receipt", "receipts"}),                    ["Finance", "Receipts"],                  90),
    (frozenset({"salary", "payslip", "paycheck"}),          ["Finance", "Payroll"],                   90),
    (frozenset({"tax", "taxes", "irs", "gst"}),             ["Finance", "Tax"],                       90),
    (frozenset({"budget", "budgets", "budgeting"}),         ["Finance", "Budgets"],                   88),
    (frozenset({"bank", "statement", "statements"}),        ["Finance", "Bank Statements"],           88),
    (frozenset({"electricity", "electric"}),                ["Finance", "Utilities", "Electricity Bills"], 90),
    (frozenset({"water", "utility", "utilities", "bill"}),  ["Finance", "Utilities"],                 87),
    (frozenset({"insurance", "policy", "premium"}),         ["Finance", "Insurance"],                 87),
    (frozenset({"loan", "emi", "mortgage"}),                ["Finance", "Loans"],                     87),
    (frozenset({"expense", "expenses", "reimbursement"}),   ["Finance", "Expenses"],                  85),
    (frozenset({"financial", "finance", "accounting",
                "accounts"}),                               ["Finance"],                              80),

    # ── Career ───────────────────────────────────────────────────────────
    (frozenset({"resume", "cv", "curriculum", "vitae"}),    ["Career", "Resumes"],                    92),
    (frozenset({"cover", "letter", "application", "job"}),  ["Career", "Cover Letters"],              88),
    (frozenset({"offer", "employment"}),                    ["Career", "Offer Letters"],              88),
    (frozenset({"performance", "review", "appraisal",
                "evaluation"}),                             ["Career", "Performance Reviews"],        85),
    (frozenset({"interview", "interviews"}),                ["Career", "Interviews"],                 85),
    (frozenset({"certification", "certificate",
                "certificates", "diploma"}),                ["Career", "Certifications"],             85),
    (frozenset({"portfolio", "portfolios"}),                ["Career", "Portfolio"],                  83),

    # ── Work / Projects ──────────────────────────────────────────────────
    (frozenset({"requirements", "requirement",
                "specification", "spec", "specs"}),         ["Work", "Requirements"],                 88),
    (frozenset({"proposal", "proposals"}),                  ["Work", "Proposals"],                    85),
    (frozenset({"contract", "contracts", "agreement",
                "agreements", "mou"}),                      ["Work", "Contracts"],                    87),
    (frozenset({"meeting", "minutes", "mom", "agenda"}),    ["Work", "Meetings"],                     85),
    (frozenset({"report", "reports"}),                      ["Work", "Reports"],                      82),
    (frozenset({"presentation", "slide", "slides",
                "deck"}),                                   ["Work", "Presentations"],                82),
    (frozenset({"marketing", "campaign", "campaigns",
                "branding", "brand"}),                      ["Work", "Marketing"],                    83),
    (frozenset({"quarterly", "q1", "q2", "q3",
                "q4", "annual"}),                           ["Work", "Quarterly Reports"],            82),
    (frozenset({"project", "projects"}),                    ["Work", "Projects"],                     75),

    # ── Learning / Education ─────────────────────────────────────────────
    # Language-specific patterns have high priority so they override generic sub-patterns
    (frozenset({"python"}),                                 ["Learning", "Python"],                   92),
    (frozenset({"javascript", "typescript", "nodejs",
                "react", "vue", "angular"}),                ["Learning", "JavaScript"],               92),
    (frozenset({"machine", "ml", "deep",
                "neural", "tensorflow", "pytorch",
                "scikit"}),                                 ["Learning", "Machine Learning"],         90),
    (frozenset({"datascience",
                "analytics", "pandas", "numpy"}),           ["Learning", "Data Science"],             88),
    (frozenset({"algorithm", "algorithms", "leetcode",
                "dsa", "structure", "structures"}),         ["Learning", "Algorithms"],               87),
    (frozenset({"tutorial", "tutorials", "guide",
                "guides", "beginner", "basics"}),           ["Learning", "Tutorials"],                84),
    (frozenset({"course", "courses", "lecture",
                "lectures", "lesson", "lessons"}),          ["Learning", "Courses"],                  85),
    (frozenset({"notes", "note", "cheatsheet",
                "cheat", "sheet", "summary", "summaries",
                "recap"}),                                  ["Learning", "Notes"],                    83),
    (frozenset({"exercise", "exercises", "practice",
                "assignment", "assignments", "homework"}),  ["Learning", "Exercises"],                84),
    (frozenset({"roadmap", "roadmaps", "syllabus"}),        ["Learning", "Roadmaps"],                 83),
    (frozenset({"ebook", "book", "books",
                "textbook"}),                               ["Learning", "Books"],                    78),
    (frozenset({"research", "paper", "papers", "thesis",
                "dissertation", "journal"}),                ["Learning", "Research Papers"],          85),

    # ── Technical ────────────────────────────────────────────────────────
    (frozenset({"sql", "database", "databases", "mysql",
                "postgres", "mongodb", "sqlite"}),          ["Technical", "Database"],                87),
    (frozenset({"schema", "schemas", "migration",
                "migrations"}),                             ["Technical", "Database"],                85),
    (frozenset({"backup", "backups"}),                      ["Technical", "Backups"],                 87),
    (frozenset({"config", "configuration",
                "settings", "setup", "env"}),               ["Technical", "Configuration"],          85),
    (frozenset({"log", "logs", "logging",
                "error", "errors", "debug"}),               ["Technical", "Logs"],                   83),
    (frozenset({"api", "swagger", "openapi",
                "endpoint", "endpoints"}),                  ["Technical", "API"],                    85),
    (frozenset({"infrastructure", "devops", "docker",
                "kubernetes", "deploy"}),                   ["Technical", "DevOps"],                 85),
    (frozenset({"script", "scripts", "automation",
                "automate"}),                               ["Technical", "Scripts"],                82),

    # ── Personal / Events ────────────────────────────────────────────────
    (frozenset({"wedding", "marriage", "bride",
                "groom"}),                                  ["Personal", "Events", "Wedding"],        88),
    (frozenset({"birthday", "party", "parties",
                "celebration", "anniversary"}),             ["Personal", "Events"],                  82),
    (frozenset({"vacation", "holiday", "holidays",
                "trip", "travel", "journey"}),              ["Personal", "Travel"],                  83),
    (frozenset({"family", "parents", "siblings"}),         ["Personal", "Family"],                  80),
    (frozenset({"medical", "health", "prescription",
                "diagnosis", "doctor", "hospital",
                "clinic"}),                                 ["Personal", "Medical"],                 87),
    (frozenset({"passport", "visa", "identity",
                "birth"}),                                  ["Personal", "Identity Documents"],      87),

    # ── Media ────────────────────────────────────────────────────────────
    (frozenset({"screenshot", "screenshots"}),             ["Media", "Screenshots"],                 85),
    (frozenset({"wallpaper", "wallpapers",
                "background", "backgrounds"}),              ["Media", "Wallpapers"],                 83),
    (frozenset({"design", "designs", "mockup",
                "mockups", "wireframe"}),                   ["Media", "Design"],                     83),
    (frozenset({"icon", "icons", "logo", "logos"}),        ["Media", "Icons and Logos"],             82),
    (frozenset({"recording", "recordings",
                "screencast"}),                             ["Media", "Recordings"],                 82),
]

# ---------------------------------------------------------------------------
# Broad fallback mapping  (extension-based category → folder)
# ---------------------------------------------------------------------------

CATEGORY_FALLBACKS: Dict[str, str] = {
    "document":     "Documents",
    "plain text":   "Documents/Text Files",
    "spreadsheet":  "Documents/Spreadsheets",
    "presentation": "Documents/Presentations",
    "image":        "Media/Images",
    "audio":        "Media/Audio",
    "video":        "Media/Video",
    "archive":      "Archives",
    "executable":   "Executables",
    "code/source":  "Technical/Code",
    "unknown":      "Misc",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """Return lowercase alphabetic tokens (min length 2) with stop-words removed."""
    raw = re.findall(r"[a-zA-Z]{2,}", text)
    return [t.lower() for t in raw if t.lower() not in STOP_WORDS]


def _cap_depth(parts: List[str]) -> List[str]:
    """Enforce the maximum folder depth."""
    return parts[:MAX_DEPTH]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ClassificationResult = Dict[str, Any]


def classify_file(
    file_record: Dict[str, Any],
    text_content: Optional[str] = None,
    all_file_records: Optional[List[Dict[str, Any]]] = None,
) -> ClassificationResult:
    """Classify a single file and return a proposed folder path.

    Args:
        file_record: The combined files+file_analysis DB row (dict). Must
            contain at least ``name``, ``relative_path``, and ``category``.
        text_content: Extracted text from the file (may be None).
        all_file_records: All file records in the scan (reserved for future
            cross-file grouping heuristics – currently unused).

    Returns:
        A dict with keys:
            ``folder_path``   – proposed destination folder relative to
                                SmartSort_Organized (e.g. ``Learning/Python``).
            ``confidence``    – ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
            ``explanation``   – human-readable reason.
            ``topic``         – detected subject / topic string.
    """
    name: str = file_record.get("name", "")
    category: str = file_record.get("category") or "unknown"

    # 1. Build token pool from filename stem + content
    stem = re.sub(r"\.[^.]+$", "", name)  # strip extension
    filename_tokens = _tokenize(stem)

    content_tokens: List[str] = []
    if text_content:
        # Bound to first 4 000 chars to limit memory use
        content_tokens = _tokenize(text_content[:4000])

    combined = set(filename_tokens) | set(content_tokens[:200])

    # 2. Match semantic patterns (collect all matches, pick best by score)
    best_score = -1
    best_parts: Optional[List[str]] = None
    best_source = "filename"

    for kw_set, folder_parts, priority in SEMANTIC_PATTERNS:
        overlap = kw_set & combined
        if not overlap:
            continue
        score = priority + len(overlap)  # small bonus for more keyword hits
        if score > best_score:
            best_score = score
            best_parts = folder_parts
            best_source = "filename" if kw_set & set(filename_tokens) else "content"

    if best_parts is not None:
        # For truly unknown files (category=unknown), require a stronger match
        # to avoid falsely classifying files into topic folders based on
        # incidental keyword overlap.
        min_score_for_unknown = 93  # must be very confident for unknown-category files
        if category == "unknown" and best_score < min_score_for_unknown:
            best_parts = None

    if best_parts is not None:
        parts = _cap_depth(best_parts)
        folder_path = "/".join(parts)

        if best_score >= 90:
            confidence = "HIGH"
        elif best_score >= 82:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        topic = parts[-1]
        explanation = (
            f"Matched pattern '{folder_path}' via {best_source} keywords "
            f"(score {best_score})."
        )
        return {
            "folder_path": folder_path,
            "confidence": confidence,
            "explanation": explanation,
            "topic": topic,
        }

    # 3. No pattern matched – fall back to broad category
    fallback_folder = CATEGORY_FALLBACKS.get(category, "Misc")
    topic = fallback_folder.split("/")[-1]
    explanation = (
        f"No specific topic detected; placed in fallback '{fallback_folder}' "
        f"based on file type '{category}'."
    )
    return {
        "folder_path": fallback_folder,
        "confidence": "LOW",
        "explanation": explanation,
        "topic": topic,
    }


def classify_file_batch(
    file_records: List[Dict[str, Any]],
    text_contents: Optional[Dict[int, str]] = None,
) -> Dict[int, ClassificationResult]:
    """Classify all files in a scan batch.

    Args:
        file_records: All combined files+analysis rows.
        text_contents: Mapping of ``file_id`` -> extracted text.

    Returns:
        Mapping of ``file_id`` -> classification result.
    """
    tc = text_contents or {}
    results: Dict[int, ClassificationResult] = {}
    for rec in file_records:
        fid = rec.get("file_id") or rec.get("id")
        if fid is None:
            continue
        text = tc.get(fid)
        results[fid] = classify_file(rec, text, file_records)
    return results
