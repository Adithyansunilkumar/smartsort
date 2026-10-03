"""Phase 7 Search Service — query processing, ranking, and similar-file detection.

Architecture
------------
1. Retrieve persisted records from SearchRepository (one batch query).
2. Build a CorpusIndex (in-memory, per-request — no persistent index needed
   at this scale; the index is cheap to rebuild).
3. Compute cosine similarity + lexical scores.
4. Merge and rank results deterministically.
5. Return typed SearchResult / SimilarFilesResponse models.

This module never reads the filesystem, re-extracts documents, or recalculates
hashes.  It operates entirely on SQLite-persisted data.

Scoring constants
-----------------
LEXICAL_FILENAME_WEIGHT  : 0.6 — filename matches are the strongest signal.
LEXICAL_PATH_WEIGHT      : 0.3 — folder/path matches are a moderate signal.
LEXICAL_CONTENT_WEIGHT   : 0.1 — content lexical presence (smaller; TF-IDF
                                  similarity already captures content quality).

Final relevance score = 0.5 * similarity_score + 0.5 * lexical_score
(Both components are independently useful; equal weight keeps the formula
simple and explainable.)
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from app.search.tokenizer import tokenize, tokenize_filename, tokenize_path
from app.search.tfidf import CorpusIndex
from app.search.repository import SearchRepository
from app.search.models import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    SimilarFileResponse,
    SimilarFilesResponse,
)

# ---------------------------------------------------------------------------
# Scoring constants (centralized, documented)
# ---------------------------------------------------------------------------

# Weight given to filename token overlap (0–1)
LEXICAL_FILENAME_WEIGHT: float = 0.6
# Weight given to path/folder token overlap (0–1)
LEXICAL_PATH_WEIGHT: float = 0.3
# Weight given to presence of query terms in content
LEXICAL_CONTENT_WEIGHT: float = 0.1

# Final score blending
SIMILARITY_BLEND: float = 0.5   # portion from TF-IDF cosine
LEXICAL_BLEND: float = 0.5       # portion from lexical field-match


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_document_tokens(record: Dict[str, Any]) -> List[str]:
    """Combine filename + path + content tokens for TF-IDF indexing."""
    tokens: List[str] = []

    # Filename tokens (weighted by repetition — repeat 3× to boost importance)
    fn_tokens = tokenize_filename(record["name"], record["extension"])
    tokens.extend(fn_tokens * 3)

    # Path tokens (repeat 2×)
    path_tokens = tokenize_path(record["relative_path"])
    tokens.extend(path_tokens * 2)

    # Content tokens (as-is, stop words removed)
    if record.get("text_content"):
        tokens.extend(tokenize(record["text_content"], remove_stop_words=True))

    return tokens


def _lexical_score(
    query_terms: Set[str],
    record: Dict[str, Any],
) -> Tuple[float, List[str], List[str]]:
    """Compute a deterministic lexical score for a single record.

    Returns:
        (score, matched_fields, matched_terms)
    """
    if not query_terms:
        return 0.0, [], []

    matched_fields: List[str] = []
    matched_terms_set: Set[str] = set()

    # --- Filename match ---
    fn_tokens = set(tokenize_filename(record["name"], record["extension"]))
    fn_overlap = query_terms & fn_tokens
    fn_fraction = len(fn_overlap) / len(query_terms) if query_terms else 0.0
    if fn_overlap:
        matched_fields.append("filename")
        matched_terms_set |= fn_overlap

    # --- Path/folder match ---
    path_tokens = set(tokenize_path(record["relative_path"]))
    path_overlap = query_terms & path_tokens
    path_fraction = len(path_overlap) / len(query_terms) if query_terms else 0.0
    if path_overlap:
        matched_fields.append("path")
        matched_terms_set |= path_overlap

    # --- Content match ---
    content_fraction = 0.0
    if record.get("text_content"):
        content_tokens = set(tokenize(record["text_content"], remove_stop_words=True))
        content_overlap = query_terms & content_tokens
        content_fraction = len(content_overlap) / len(query_terms) if query_terms else 0.0
        if content_overlap:
            matched_fields.append("content")
            matched_terms_set |= content_overlap

    score = (
        fn_fraction * LEXICAL_FILENAME_WEIGHT
        + path_fraction * LEXICAL_PATH_WEIGHT
        + content_fraction * LEXICAL_CONTENT_WEIGHT
    )

    return score, matched_fields, sorted(matched_terms_set)


def _apply_filters(records: List[Dict], request: SearchRequest) -> List[Dict]:
    """Apply optional request filters to the candidate set."""
    if request.category:
        records = [r for r in records if r.get("category") == request.category]
    if request.extension:
        ext = request.extension.lower()
        records = [r for r in records if r["extension"].lower() == ext]
    if request.min_size_bytes is not None:
        records = [r for r in records if r["size_bytes"] >= request.min_size_bytes]
    if request.max_size_bytes is not None:
        records = [r for r in records if r["size_bytes"] <= request.max_size_bytes]
    return records


# ---------------------------------------------------------------------------
# Public search entry-point
# ---------------------------------------------------------------------------

def search_scan(request: SearchRequest) -> SearchResponse:
    """Execute a full search against a persisted scan.

    Steps:
    1. Validate the scan exists.
    2. Fetch all records in one batch query.
    3. Apply optional filters.
    4. Build in-memory TF-IDF corpus index.
    5. Compute cosine similarity for every document.
    6. Compute lexical score for every document.
    7. Merge, rank deterministically, cap at limit.
    8. Return SearchResponse.
    """
    if not SearchRepository.scan_exists(request.scan_id):
        raise ValueError(f"Scan '{request.scan_id}' not found.")

    records = SearchRepository.get_searchable_records(request.scan_id)
    records = _apply_filters(records, request)

    if not records:
        return SearchResponse(
            scan_id=request.scan_id,
            query=request.query,
            total_results=0,
            results=[],
        )

    # Tokenize query (no stop-word removal so single-word stop-word queries work)
    query_tokens = tokenize(request.query, remove_stop_words=False)
    query_terms = set(query_tokens)

    # Build corpus index
    doc_tokens: Dict[int, List[str]] = {
        r["file_id"]: _build_document_tokens(r) for r in records
    }
    index = CorpusIndex()
    index.build(doc_tokens)

    # Cosine scores: {file_id: score}
    sim_scores: Dict[int, float] = dict(index.query(query_tokens))

    # Build result list
    results: List[SearchResult] = []
    for record in records:
        fid = record["file_id"]
        sim_score = sim_scores.get(fid, 0.0)
        lex_score, matched_fields, matched_terms = _lexical_score(query_terms, record)

        relevance = SIMILARITY_BLEND * sim_score + LEXICAL_BLEND * lex_score

        # Skip files with no match signal at all
        if relevance == 0.0 and sim_score == 0.0 and lex_score == 0.0:
            continue

        results.append(
            SearchResult(
                file_id=fid,
                scan_id=record["scan_id"],
                name=record["name"],
                relative_path=record["relative_path"],
                extension=record["extension"],
                category=record.get("category"),
                size_bytes=record["size_bytes"],
                modified_at=record["modified_at"],
                relevance_score=round(relevance, 6),
                similarity_score=round(sim_score, 6),
                lexical_score=round(lex_score, 6),
                matched_fields=matched_fields,
                matched_terms=matched_terms,
                content_available=bool(record.get("text_content")),
                has_duplicates=bool(record.get("has_duplicates")),
            )
        )

    # Deterministic sort: relevance desc → similarity desc → path asc → file_id asc
    results.sort(
        key=lambda r: (-r.relevance_score, -r.similarity_score, r.relative_path, r.file_id)
    )
    results = results[: request.limit]

    return SearchResponse(
        scan_id=request.scan_id,
        query=request.query,
        total_results=len(results),
        results=results,
    )


# ---------------------------------------------------------------------------
# Similar-file entry-point
# ---------------------------------------------------------------------------

def find_similar_files(
    scan_id: str,
    file_id: int,
    limit: int = 10,
) -> SimilarFilesResponse:
    """Find files similar to a target using TF-IDF cosine similarity.

    Similarity is based purely on token/vector evidence (content + filename
    + path tokens).  Returns an empty result if the target has no meaningful
    searchable content.

    The target file itself is excluded from results.
    Files from other scans are never returned.
    """
    if not SearchRepository.scan_exists(scan_id):
        raise ValueError(f"Scan '{scan_id}' not found.")

    target = SearchRepository.get_file_by_id(scan_id, file_id)
    if target is None:
        raise LookupError(f"File ID {file_id} not found in scan '{scan_id}'.")

    records = SearchRepository.get_searchable_records(scan_id)

    # Build corpus index over all documents
    doc_tokens: Dict[int, List[str]] = {
        r["file_id"]: _build_document_tokens(r) for r in records
    }
    index = CorpusIndex()
    index.build(doc_tokens)

    # Use the target's own TF-IDF vector as the "query"
    target_tokens = doc_tokens.get(file_id, [])
    if not target_tokens:
        # No content to derive similarity from
        return SimilarFilesResponse(
            scan_id=scan_id,
            target_file_id=file_id,
            target_name=target["name"],
            similar_files=[],
        )

    scores = index.query(target_tokens)

    # Build file lookup
    record_map: Dict[int, Dict] = {r["file_id"]: r for r in records}

    similar: List[SimilarFileResponse] = []
    for fid, score in scores:
        if fid == file_id:
            continue   # Exclude the target itself
        if score == 0.0:
            continue   # No meaningful similarity
        rec = record_map.get(fid)
        if rec is None:
            continue
        similar.append(
            SimilarFileResponse(
                file_id=fid,
                name=rec["name"],
                relative_path=rec["relative_path"],
                extension=rec["extension"],
                category=rec.get("category"),
                size_bytes=rec["size_bytes"],
                modified_at=rec["modified_at"],
                similarity_score=round(score, 6),
            )
        )
        if len(similar) >= limit:
            break

    return SimilarFilesResponse(
        scan_id=scan_id,
        target_file_id=file_id,
        target_name=target["name"],
        similar_files=similar,
    )
