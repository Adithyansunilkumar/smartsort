"""Content-aware, deterministic file classification engine for SmartSort.

Classification priority (first confident match wins):

1. **Name and content hints (purpose)** — filename tokens and extracted text
   are checked against keyword patterns for Financial, Legal, Career,
   Screenshots, Photos, etc.
2. **MIME / magic-number mismatch** — if extension and MIME disagree, trust
   the content, flag ``extension_mismatch``, and route to ``_Review``.
3. **Extension map** — case-insensitive lookup including compound extensions
   like ``.tar.gz``.
4. **Fallback** — unknown or conflicting → ``_Review``.

Zero-byte files → ``_Empty``.
Partial downloads (``.crdownload``, ``.part``, ``.tmp``, ``.download``) are
skipped by the planner (not classified here).

All constants and helper functions live in ``rules.py``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.organization.rules import (
    MAX_DEPTH,
    NAME_HINT_PATTERNS,
    CONTENT_HINT_KEYWORDS,
    STOP_WORDS,
    EXTENSION_MAP,
    COMPOUND_EXTENSION_MAP,
    get_extension_category,
    get_mime_category,
    check_extension_mismatch,
    detect_filename_date,
    match_existing_folder,
)

# ---------------------------------------------------------------------------
# Semantic pattern tables (kept from original for broad topic detection)
# Each entry: (keywords_frozenset, folder_path_components, priority, rule_name)
# ---------------------------------------------------------------------------

SEMANTIC_PATTERNS: List[tuple] = [
    # ── Finance ──────────────────────────────────────────────────────────
    (frozenset({"invoice", "invoices"}),                    ["Documents", "Financial"],              90, "semantic_invoice"),
    (frozenset({"receipt", "receipts"}),                    ["Documents", "Financial"],              90, "semantic_receipt"),
    (frozenset({"salary", "payslip", "paycheck"}),          ["Documents", "Financial"],              90, "semantic_payroll"),
    (frozenset({"tax", "taxes", "irs", "gst"}),             ["Documents", "Financial"],              90, "semantic_tax"),
    (frozenset({"budget", "budgets", "budgeting"}),         ["Documents", "Financial"],              88, "semantic_budget"),
    (frozenset({"bank", "statement", "statements"}),        ["Documents", "Financial"],              88, "semantic_statement"),
    (frozenset({"electricity", "electric"}),                ["Documents", "Financial"],              90, "semantic_utility"),
    (frozenset({"water", "utility", "utilities", "bill"}),  ["Documents", "Financial"],              87, "semantic_utility"),
    (frozenset({"insurance", "policy", "premium"}),         ["Documents", "Financial"],              87, "semantic_insurance"),
    (frozenset({"loan", "emi", "mortgage"}),                ["Documents", "Financial"],              87, "semantic_loan"),
    (frozenset({"expense", "expenses", "reimbursement"}),   ["Documents", "Financial"],              85, "semantic_expense"),
    (frozenset({"financial", "finance", "accounting",
                "accounts"}),                               ["Documents", "Financial"],              80, "semantic_finance"),

    # ── Legal ────────────────────────────────────────────────────────────
    (frozenset({"contract", "contracts", "agreement",
                "agreements", "mou"}),                      ["Documents", "Legal"],                  87, "semantic_contract"),
    (frozenset({"nda"}),                                    ["Documents", "Legal"],                  90, "semantic_nda"),
    (frozenset({"terms"}),                                  ["Documents", "Legal"],                  85, "semantic_terms"),

    # ── Career ───────────────────────────────────────────────────────────
    (frozenset({"resume", "cv", "curriculum", "vitae"}),    ["Documents", "Career"],                 92, "semantic_resume"),
    (frozenset({"cover", "letter", "application", "job"}),  ["Documents", "Career"],                 88, "semantic_cover_letter"),
    (frozenset({"offer", "employment"}),                    ["Documents", "Career"],                 88, "semantic_offer"),
    (frozenset({"performance", "review", "appraisal",
                "evaluation"}),                             ["Documents", "Career"],                 85, "semantic_review"),
    (frozenset({"interview", "interviews"}),                ["Documents", "Career"],                 85, "semantic_interview"),
    (frozenset({"certification", "certificate",
                "certificates", "diploma"}),                ["Documents", "Career"],                 85, "semantic_certification"),
    (frozenset({"portfolio", "portfolios"}),                ["Documents", "Career"],                 83, "semantic_portfolio"),

    # ── Work / Projects ──────────────────────────────────────────────────
    (frozenset({"requirements", "requirement",
                "specification", "spec", "specs"}),         ["Work", "Requirements"],                88, "semantic_requirements"),
    (frozenset({"proposal", "proposals"}),                  ["Work", "Proposals"],                   85, "semantic_proposal"),
    (frozenset({"meeting", "minutes", "mom", "agenda"}),    ["Work", "Meetings"],                    85, "semantic_meeting"),
    (frozenset({"report", "reports"}),                      ["Work", "Reports"],                     82, "semantic_report"),
    (frozenset({"presentation", "slide", "slides",
                "deck"}),                                   ["Work", "Presentations"],               82, "semantic_presentation"),
    (frozenset({"marketing", "campaign", "campaigns",
                "branding", "brand"}),                      ["Work", "Marketing"],                   83, "semantic_marketing"),
    (frozenset({"quarterly", "q1", "q2", "q3",
                "q4", "annual"}),                           ["Work", "Quarterly Reports"],           82, "semantic_quarterly"),
    (frozenset({"project", "projects"}),                    ["Work", "Projects"],                    75, "semantic_project"),

    # ── Learning / Education ─────────────────────────────────────────────
    (frozenset({"python"}),                                 ["Learning", "Python"],                  92, "semantic_python"),
    (frozenset({"javascript", "typescript", "nodejs",
                "react", "vue", "angular"}),                ["Learning", "JavaScript"],              92, "semantic_javascript"),
    (frozenset({"machine", "ml", "deep",
                "neural", "tensorflow", "pytorch",
                "scikit"}),                                 ["Learning", "Machine Learning"],        90, "semantic_ml"),
    (frozenset({"datascience",
                "analytics", "pandas", "numpy"}),           ["Learning", "Data Science"],            88, "semantic_datascience"),
    (frozenset({"algorithm", "algorithms", "leetcode",
                "dsa", "structure", "structures"}),         ["Learning", "Algorithms"],              87, "semantic_algorithms"),
    (frozenset({"tutorial", "tutorials", "guide",
                "guides", "beginner", "basics"}),           ["Learning", "Tutorials"],               84, "semantic_tutorial"),
    (frozenset({"course", "courses", "lecture",
                "lectures", "lesson", "lessons"}),          ["Learning", "Courses"],                 85, "semantic_course"),
    (frozenset({"notes", "note", "cheatsheet",
                "cheat", "sheet", "summary", "summaries",
                "recap"}),                                  ["Learning", "Notes"],                   83, "semantic_notes"),
    (frozenset({"exercise", "exercises", "practice",
                "assignment", "assignments", "homework"}),  ["Learning", "Exercises"],               84, "semantic_exercise"),
    (frozenset({"roadmap", "roadmaps", "syllabus"}),        ["Learning", "Roadmaps"],                83, "semantic_roadmap"),
    (frozenset({"ebook", "book", "books",
                "textbook"}),                               ["Learning", "Books"],                   78, "semantic_book"),
    (frozenset({"research", "paper", "papers", "thesis",
                "dissertation", "journal"}),                ["Learning", "Research Papers"],         85, "semantic_research"),

    # ── Technical ────────────────────────────────────────────────────────
    (frozenset({"sql", "database", "databases", "mysql",
                "postgres", "mongodb", "sqlite"}),          ["Technical", "Database"],               87, "semantic_database"),
    (frozenset({"schema", "schemas", "migration",
                "migrations"}),                             ["Technical", "Database"],               85, "semantic_schema"),
    (frozenset({"backup", "backups"}),                      ["Technical", "Backups"],                87, "semantic_backup"),
    (frozenset({"config", "configuration",
                "settings", "setup", "env"}),               ["Technical", "Configuration"],          85, "semantic_config"),
    (frozenset({"log", "logs", "logging",
                "error", "errors", "debug"}),               ["Technical", "Logs"],                   83, "semantic_log"),
    (frozenset({"api", "swagger", "openapi",
                "endpoint", "endpoints"}),                  ["Technical", "API"],                    85, "semantic_api"),
    (frozenset({"infrastructure", "devops", "docker",
                "kubernetes", "deploy"}),                   ["Technical", "DevOps"],                 85, "semantic_devops"),
    (frozenset({"script", "scripts", "automation",
                "automate"}),                               ["Technical", "Scripts"],                82, "semantic_scripts"),

    # ── Personal / Events ────────────────────────────────────────────────
    (frozenset({"wedding", "marriage", "bride",
                "groom"}),                                  ["Personal", "Events", "Wedding"],       88, "semantic_wedding"),
    (frozenset({"birthday", "party", "parties",
                "celebration", "anniversary"}),             ["Personal", "Events"],                  82, "semantic_event"),
    (frozenset({"vacation", "holiday", "holidays",
                "trip", "travel", "journey"}),              ["Personal", "Travel"],                  83, "semantic_travel"),
    (frozenset({"family", "parents", "siblings"}),          ["Personal", "Family"],                  80, "semantic_family"),
    (frozenset({"medical", "health", "prescription",
                "diagnosis", "doctor", "hospital",
                "clinic"}),                                 ["Personal", "Medical"],                 87, "semantic_medical"),
    (frozenset({"passport", "visa", "identity",
                "birth"}),                                  ["Personal", "Identity Documents"],      87, "semantic_identity"),

    # ── Media ────────────────────────────────────────────────────────────
    (frozenset({"screenshot", "screenshots"}),              ["Images", "Screenshots"],               85, "semantic_screenshot"),
    (frozenset({"wallpaper", "wallpapers",
                "background", "backgrounds"}),              ["Images", "Wallpapers"],                83, "semantic_wallpaper"),
    (frozenset({"design", "designs", "mockup",
                "mockups", "wireframe"}),                   ["Images", "Design"],                    83, "semantic_design"),
    (frozenset({"icon", "icons", "logo", "logos"}),         ["Images", "Icons and Logos"],            82, "semantic_icons"),
    (frozenset({"recording", "recordings",
                "screencast"}),                             ["Videos", "Recordings"],                82, "semantic_recording"),
]

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

    Tiers checked in order (first confident match wins):
    1. Zero-byte → ``_Empty``
    2. Name hints (purpose keywords in filename)
    3. MIME/extension mismatch → ``_Review`` with ``extension_mismatch`` flag
    4. Extension map
    5. Semantic patterns (filename + content tokens)
    6. Content hint keywords (extracted text)
    7. Fallback → ``_Review``

    Returns a dict with keys:
        ``folder_path``       – proposed destination folder
        ``confidence``        – numeric 0.0–1.0
        ``confidence_label``  – ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``
        ``explanation``       – human-readable reason
        ``rule``              – machine-readable rule identifier
        ``flags``             – list of string flags (e.g. ``extension_mismatch``)
        ``topic``             – detected subject / topic string
        ``needs_review``      – True if confidence < 0.8 or routed to _Review
    """
    name: str = file_record.get("name", "")
    category: str = file_record.get("category") or "unknown"
    mime_type: str = file_record.get("mime_type") or ""
    flags: List[str] = []

    # Determine size — only trigger _Empty when explicitly zero
    raw_size = file_record.get("size_bytes") or file_record.get("size")
    size_bytes: int = raw_size if raw_size is not None else -1  # -1 = unknown

    # ── Tier 0: Zero-byte → _Empty ──────────────────────────────────────
    if size_bytes == 0:
        return _result("_Empty", 0.95, "zero_byte",
                        f"File '{name}' is empty (0 bytes).", flags=flags)

    # ── Tier 1: Name hint patterns (purpose) ────────────────────────────
    stem = re.sub(r"\.[^.]+$", "", name)
    ext_lower = name[len(stem):].lower() if len(name) > len(stem) else ""

    for pat, folder, conf, rule in NAME_HINT_PATTERNS:
        if rule == "name_camera_prefix":
            # Only match camera prefixes for image extensions
            image_exts = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}
            if ext_lower not in image_exts:
                continue
        if pat.search(stem):
            return _result(folder, conf, rule,
                            f"Name contains '{pat.pattern}', treated as {folder.split('/')[-1]}.",
                            flags=flags, topic=folder.split("/")[-1])

    # ── Tier 2: MIME / extension mismatch ───────────────────────────────
    if mime_type and check_extension_mismatch(name, mime_type):
        flags.append("extension_mismatch")
        mime_cat = get_mime_category(mime_type)
        return _result("_Review", 0.50, "mime_mismatch",
                        f"Extension suggests '{get_extension_category(name)}' but "
                        f"MIME type '{mime_type}' suggests '{mime_cat}'. "
                        f"Routed to _Review for manual inspection.",
                        flags=flags)

    # ── Tier 3: Extension map ───────────────────────────────────────────
    ext_category = get_extension_category(name)

    # ── Tier 4: Semantic patterns (filename + content tokens) ───────────
    filename_tokens = _tokenize(stem)
    content_tokens: List[str] = []
    if text_content:
        content_tokens = _tokenize(text_content[:4000])

    combined = set(filename_tokens) | set(content_tokens[:200])

    best_score = -1
    best_parts: Optional[List[str]] = None
    best_rule = ""
    best_source = "filename"

    for kw_set, folder_parts, priority, rule_name in SEMANTIC_PATTERNS:
        overlap = kw_set & combined
        if not overlap:
            continue
        score = priority + len(overlap)
        if score > best_score:
            best_score = score
            best_parts = folder_parts
            best_rule = rule_name
            best_source = "filename" if kw_set & set(filename_tokens) else "content"

    if best_parts is not None:
        # For truly unknown files (category=unknown), require stronger match
        min_score_for_unknown = 93
        if category == "unknown" and best_score < min_score_for_unknown:
            best_parts = None

    if best_parts is not None:
        parts = _cap_depth(best_parts)
        folder_path = "/".join(parts)

        if best_score >= 90:
            conf = 0.90
        elif best_score >= 82:
            conf = 0.75
        else:
            conf = 0.55

        topic = parts[-1]
        explanation = (
            f"Matched pattern '{folder_path}' via {best_source} keywords "
            f"(score {best_score})."
        )
        return _result(folder_path, conf, best_rule, explanation,
                        flags=flags, topic=topic)

    # ── Tier 5: Content hint keywords ───────────────────────────────────
    if text_content:
        text_lower = text_content[:4000].lower()
        best_kw_conf = 0.0
        best_kw_result = None
        for keyword, (folder, conf, rule) in CONTENT_HINT_KEYWORDS.items():
            count = text_lower.count(keyword)
            if count >= 2:
                adj_conf = min(conf + 0.02 * count, 0.95)
                if adj_conf > best_kw_conf:
                    best_kw_conf = adj_conf
                    best_kw_result = (folder, adj_conf, rule, keyword)
        if best_kw_result:
            folder, conf, rule, kw = best_kw_result
            return _result(folder, conf, rule,
                            f"Content contains keyword '{kw}' multiple times, "
                            f"classified as {folder}.",
                            flags=flags, topic=folder.split("/")[-1])

    # ── Tier 6: Extension-based fallback ────────────────────────────────
    if ext_category:
        return _result(ext_category, 0.70, "extension_map",
                        f"Classified by extension '{ext_lower}' as {ext_category}.",
                        flags=flags, topic=ext_category)

    # ── Tier 7: True fallback → _Review ─────────────────────────────────
    return _result("_Review", 0.30, "fallback_review",
                    f"No confident classification for '{name}'; sent to _Review.",
                    flags=flags)


def _result(
    folder_path: str,
    confidence: float,
    rule: str,
    explanation: str,
    *,
    flags: Optional[List[str]] = None,
    topic: Optional[str] = None,
) -> ClassificationResult:
    """Build a standardised classification result dict."""
    if confidence >= 0.8:
        label = "HIGH"
    elif confidence >= 0.5:
        label = "MEDIUM"
    else:
        label = "LOW"

    needs_review = confidence < 0.8 or folder_path == "_Review"
    if topic is None:
        topic = folder_path.split("/")[-1] if "/" in folder_path else folder_path

    return {
        "folder_path": folder_path,
        "confidence": confidence,
        "confidence_label": label,
        "explanation": explanation,
        "rule": rule,
        "flags": flags or [],
        "topic": topic,
        "needs_review": needs_review,
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
