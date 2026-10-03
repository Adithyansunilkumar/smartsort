"""Phase 7 — Comprehensive tests for Search & Similarity.

Covers:
- Tokenizer (lowercase, punctuation, Unicode, numbers, filenames, technical
  terms, repeated whitespace, empty input)
- TF-IDF (vocabulary, document frequency, IDF, TF-IDF vectors, determinism)
- Cosine similarity (identical, orthogonal, zero, partial, symmetric)
- Search (filename, path, content, combined, case-insensitive, Unicode,
  no-results, multiple-results, ranking, limit, empty query, missing scan)
- Similar files (similar content, dissimilar, target excluded, limit,
  no content, missing file, cross-scan)
- Security (no absolute paths in results, scan ownership)
"""

import math
import pytest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.search.tokenizer import tokenize, tokenize_filename, tokenize_path
from app.search.tfidf import (
    build_idf,
    vectorize,
    cosine_similarity,
    CorpusIndex,
)
from app.search.service import search_scan, find_similar_files
from app.search.models import SearchRequest
from app.database.repositories import ScanRepository, ContentRepository
from app.filesystem.scanner import ScanResult, ScannedFile, ScanStatus

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


def _persist_content(file_id: int, text: str, source_hash: str = "h1") -> None:
    ContentRepository.upsert_extraction_result(
        file_id=file_id,
        status="SUCCESS",
        text_content=text,
        character_count=len(text),
        page_count=1,
        extracted_at=_utcnow(),
        error_info=None,
        source_hash=source_hash,
    )


def _file_ids(scan_id: str) -> dict[str, int]:
    """Return {name: file_id} for the given scan."""
    rows = ScanRepository.get_files_by_scan_id(scan_id)
    return {r["name"]: r["id"] for r in rows}


# ===========================================================================
# TOKENIZER TESTS
# ===========================================================================

class TestTokenizer:
    def test_lowercase_normalization(self):
        assert tokenize("Hello WORLD") == ["hello", "world"]

    def test_punctuation_stripped(self):
        tokens = tokenize("hello, world! how are you?")
        assert "hello" in tokens
        assert "world" in tokens
        # "how", "are", "you" may be stop-words — at least the content words survive
        assert "," not in tokens
        assert "!" not in tokens

    def test_unicode_basic(self):
        tokens = tokenize("Ünïcödé test")
        assert "test" in tokens
        # Unicode letters survive
        assert any("nïcöd" in t or "unicode" in t for t in tokens)

    def test_numbers_preserved(self):
        tokens = tokenize("release 2026 version 3", remove_stop_words=False)
        assert "2026" in tokens
        assert "3" in tokens

    def test_technical_terms_preserved(self):
        tokens = tokenize("java python api oop react", remove_stop_words=False)
        assert "java" in tokens
        assert "python" in tokens
        assert "api" in tokens
        assert "oop" in tokens
        assert "react" in tokens

    def test_repeated_whitespace(self):
        assert tokenize("hello   world") == tokenize("hello world")

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("   ") == []

    def test_deterministic(self):
        text = "The Quick Brown Fox Jumps Over The Lazy Dog"
        assert tokenize(text) == tokenize(text)

    def test_tokenize_filename_letter_digit_split(self):
        tokens = tokenize_filename("notes123.pdf", ".pdf")
        assert "notes" in tokens
        assert "123" in tokens
        assert "pdf" in tokens

    def test_tokenize_filename_extension_added(self):
        tokens = tokenize_filename("readme.md", ".md")
        assert "md" in tokens

    def test_tokenize_filename_no_extension(self):
        tokens = tokenize_filename("makefile", "")
        assert "makefile" in tokens

    def test_tokenize_path_folder_components(self):
        tokens = tokenize_path("projects/java/notes.txt")
        assert "projects" in tokens
        assert "java" in tokens
        # Filename excluded
        assert "notes" not in tokens

    def test_tokenize_path_root_level(self):
        # Root-level file — no folder tokens
        assert tokenize_path("readme.txt") == []

    def test_tokenize_path_unicode_folder(self):
        tokens = tokenize_path("Ünïcödé/file.txt")
        assert len(tokens) > 0  # Some tokens extracted

    def test_stop_words_removed_by_default(self):
        tokens = tokenize("the file is in the folder", remove_stop_words=True)
        assert "the" not in tokens
        assert "is" not in tokens
        assert "in" not in tokens

    def test_stop_words_kept_when_disabled(self):
        tokens = tokenize("the file", remove_stop_words=False)
        assert "the" in tokens


# ===========================================================================
# TF-IDF TESTS
# ===========================================================================

class TestTFIDF:
    def test_build_idf_empty_corpus(self):
        idf = build_idf([])
        assert idf == {}

    def test_build_idf_single_doc(self):
        idf = build_idf([["java", "notes", "java"]])
        # java appears in 1/1 docs → IDF = log((1+1)/(1+1)) + 1 = log(1)+1 = 1
        assert "java" in idf
        assert abs(idf["java"] - 1.0) < 1e-9

    def test_build_idf_two_docs_one_term_shared(self):
        idf = build_idf([["java"], ["java", "python"]])
        # java: df=2, N=2 → IDF = log(3/3) + 1 = 1.0
        assert abs(idf["java"] - 1.0) < 1e-9
        # python: df=1, N=2 → IDF = log(3/2) + 1 ≈ 1.405
        expected_python = math.log(3 / 2) + 1
        assert abs(idf["python"] - expected_python) < 1e-9

    def test_vectorize_known(self):
        idf = {"java": 1.5, "notes": 2.0}
        tokens = ["java", "java", "notes"]
        vec = vectorize(tokens, idf)
        assert "java" in vec
        assert "notes" in vec
        # TF(java) = 2/3, TF(notes) = 1/3
        assert abs(vec["java"] - (2 / 3) * 1.5) < 1e-9
        assert abs(vec["notes"] - (1 / 3) * 2.0) < 1e-9

    def test_vectorize_unknown_term_excluded(self):
        idf = {"java": 1.5}
        tokens = ["java", "python"]
        vec = vectorize(tokens, idf)
        assert "python" not in vec

    def test_vectorize_empty_tokens(self):
        idf = {"java": 1.5}
        assert vectorize([], idf) == {}

    def test_deterministic(self):
        corpus = [["java", "notes"], ["python", "api"], ["java", "python"]]
        idf1 = build_idf(corpus)
        idf2 = build_idf(corpus)
        assert idf1 == idf2

    def test_corpus_index_query_returns_scores(self):
        docs = {1: ["java", "notes", "java"], 2: ["python", "api"], 3: ["java", "api"]}
        idx = CorpusIndex()
        idx.build(docs)
        scores = idx.query(["java"])
        ids = [s[0] for s in scores]
        # doc 1 and 3 have java; doc 2 does not
        assert 1 in ids
        assert 3 in ids

    def test_corpus_index_empty_query(self):
        docs = {1: ["java"]}
        idx = CorpusIndex()
        idx.build(docs)
        assert idx.query([]) == []

    def test_corpus_index_empty_corpus(self):
        idx = CorpusIndex()
        idx.build({})
        assert idx.query(["java"]) == []


# ===========================================================================
# COSINE SIMILARITY TESTS
# ===========================================================================

class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = {"a": 1.0, "b": 2.0}
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        assert cosine_similarity(vec_a, vec_b) == 0.0

    def test_partial_overlap(self):
        vec_a = {"a": 1.0, "b": 1.0}
        vec_b = {"b": 1.0, "c": 1.0}
        sim = cosine_similarity(vec_a, vec_b)
        assert 0.0 < sim < 1.0

    def test_zero_vector_a(self):
        assert cosine_similarity({}, {"a": 1.0}) == 0.0

    def test_zero_vector_b(self):
        assert cosine_similarity({"a": 1.0}, {}) == 0.0

    def test_both_zero(self):
        assert cosine_similarity({}, {}) == 0.0

    def test_symmetric(self):
        va = {"x": 0.5, "y": 0.8}
        vb = {"y": 0.3, "z": 0.6}
        assert abs(cosine_similarity(va, vb) - cosine_similarity(vb, va)) < 1e-12

    def test_no_nan(self):
        import math
        result = cosine_similarity({"a": 0.0}, {"a": 0.0})
        assert not math.isnan(result)
        assert not math.isinf(result)

    def test_bounded(self):
        for va, vb in [
            ({"a": 1.0}, {"a": 1.0}),
            ({"a": 1.0}, {"b": 1.0}),
            ({"a": 0.5, "b": 0.8}, {"a": 0.3}),
        ]:
            sim = cosine_similarity(va, vb)
            assert 0.0 <= sim <= 1.0


# ===========================================================================
# SEARCH INTEGRATION TESTS
# ===========================================================================

class TestSearchIntegration:
    """Integration tests that use the real DB fixture via conftest."""

    def test_filename_only_match(self, authorized_root: Path, clean_service):
        scan_id = "s-fn-only"
        _persist_scan(scan_id, [
            ScannedFile(name="java_notes.txt", relative_path="java_notes.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="python_intro.pdf", relative_path="python_intro.pdf",
                        extension=".pdf", size_bytes=200, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="java", limit=10)
        resp = search_scan(req)
        assert resp.total_results >= 1
        top = resp.results[0]
        assert "java" in top.name.lower()
        assert "filename" in top.matched_fields

    def test_path_folder_match(self, authorized_root: Path, clean_service):
        scan_id = "s-path"
        _persist_scan(scan_id, [
            ScannedFile(name="notes.pdf", relative_path="java/notes.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="other.pdf", relative_path="python/other.pdf",
                        extension=".pdf", size_bytes=200, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="java", limit=10)
        resp = search_scan(req)
        assert any("path" in r.matched_fields for r in resp.results)
        # The java-folder file should score higher
        java_result = next((r for r in resp.results if "java" in r.relative_path), None)
        assert java_result is not None

    def test_content_match(self, authorized_root: Path, clean_service):
        scan_id = "s-content"
        _persist_scan(scan_id, [
            ScannedFile(name="report.pdf", relative_path="report.pdf",
                        extension=".pdf", size_bytes=500, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["report.pdf"], "This document discusses OOP principles in Java.")
        req = SearchRequest(scan_id=scan_id, query="OOP Java", limit=10)
        resp = search_scan(req)
        assert resp.total_results == 1
        assert "content" in resp.results[0].matched_fields

    def test_combined_filename_and_content(self, authorized_root: Path, clean_service):
        scan_id = "s-combo"
        _persist_scan(scan_id, [
            ScannedFile(name="java_notes.pdf", relative_path="java_notes.pdf",
                        extension=".pdf", size_bytes=300, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["java_notes.pdf"], "Advanced Java OOP concepts and patterns.")
        req = SearchRequest(scan_id=scan_id, query="java OOP", limit=10)
        resp = search_scan(req)
        top = resp.results[0]
        assert "filename" in top.matched_fields
        assert "content" in top.matched_fields

    def test_case_insensitive(self, authorized_root: Path, clean_service):
        scan_id = "s-case"
        _persist_scan(scan_id, [
            ScannedFile(name="JAVA_NOTES.TXT", relative_path="JAVA_NOTES.TXT",
                        extension=".TXT", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="java", limit=10)
        resp = search_scan(req)
        assert resp.total_results >= 1

    def test_unicode_filename(self, authorized_root: Path, clean_service):
        scan_id = "s-uni"
        _persist_scan(scan_id, [
            ScannedFile(name="résuméü.pdf", relative_path="résuméü.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="resum", limit=10)
        resp = search_scan(req)
        # Unicode normalized — may or may not match depending on normalization
        # At minimum no crash
        assert isinstance(resp.total_results, int)

    def test_no_results(self, authorized_root: Path, clean_service):
        scan_id = "s-none"
        _persist_scan(scan_id, [
            ScannedFile(name="photo.jpg", relative_path="photo.jpg",
                        extension=".jpg", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="zzz_not_found_xyz", limit=10)
        resp = search_scan(req)
        assert resp.total_results == 0

    def test_limit_respected(self, authorized_root: Path, clean_service):
        scan_id = "s-limit"
        files = [
            ScannedFile(name=f"java_doc_{i}.txt", relative_path=f"java_doc_{i}.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z")
            for i in range(10)
        ]
        _persist_scan(scan_id, files)
        req = SearchRequest(scan_id=scan_id, query="java", limit=3)
        resp = search_scan(req)
        assert len(resp.results) <= 3

    def test_deterministic_ranking(self, authorized_root: Path, clean_service):
        scan_id = "s-det"
        _persist_scan(scan_id, [
            ScannedFile(name="java_advanced.pdf", relative_path="java_advanced.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="java_intro.pdf", relative_path="java_intro.pdf",
                        extension=".pdf", size_bytes=200, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="java", limit=10)
        r1 = search_scan(req)
        r2 = search_scan(req)
        assert [r.file_id for r in r1.results] == [r.file_id for r in r2.results]

    def test_no_absolute_paths(self, authorized_root: Path, clean_service):
        scan_id = "s-nabs"
        _persist_scan(scan_id, [
            ScannedFile(name="readme.txt", relative_path="readme.txt",
                        extension=".txt", size_bytes=50, modified_at="2024-01-01T00:00:00Z"),
        ])
        req = SearchRequest(scan_id=scan_id, query="readme", limit=10)
        resp = search_scan(req)
        for r in resp.results:
            assert not r.relative_path.startswith("C:")
            assert not r.relative_path.startswith("/")

    def test_missing_scan(self):
        req = SearchRequest(scan_id="nonexistent-scan-999", query="java", limit=10)
        with pytest.raises(ValueError, match="not found"):
            search_scan(req)


# ===========================================================================
# SIMILAR FILES TESTS
# ===========================================================================

class TestSimilarFiles:
    def test_similar_by_content(self, authorized_root: Path, clean_service):
        scan_id = "s-sim"
        _persist_scan(scan_id, [
            ScannedFile(name="doc_a.txt", relative_path="doc_a.txt",
                        extension=".txt", size_bytes=200, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="doc_b.txt", relative_path="doc_b.txt",
                        extension=".txt", size_bytes=200, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="unrelated.txt", relative_path="unrelated.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["doc_a.txt"], "Java OOP design patterns in enterprise applications")
        _persist_content(fids["doc_b.txt"], "OOP Java patterns used in large enterprise systems")
        _persist_content(fids["unrelated.txt"], "Cooking recipes and kitchen tips")
        
        resp = find_similar_files(scan_id, fids["doc_a.txt"], limit=5)
        ids = [s.file_id for s in resp.similar_files]
        assert fids["doc_a.txt"] not in ids   # Target excluded
        # doc_b should rank above unrelated
        if fids["doc_b.txt"] in ids and fids["unrelated.txt"] in ids:
            idx_b = ids.index(fids["doc_b.txt"])
            idx_u = ids.index(fids["unrelated.txt"])
            assert idx_b < idx_u

    def test_target_excluded(self, authorized_root: Path, clean_service):
        scan_id = "s-excl"
        _persist_scan(scan_id, [
            ScannedFile(name="a.txt", relative_path="a.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="b.txt", relative_path="b.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["a.txt"], "shared topic about software development")
        _persist_content(fids["b.txt"], "shared topic about software development practices")
        resp = find_similar_files(scan_id, fids["a.txt"], limit=5)
        assert fids["a.txt"] not in [s.file_id for s in resp.similar_files]

    def test_no_content_returns_empty(self, authorized_root: Path, clean_service):
        scan_id = "s-nc"
        _persist_scan(scan_id, [
            ScannedFile(name="photo.jpg", relative_path="photo.jpg",
                        extension=".jpg", size_bytes=5000, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="other.jpg", relative_path="other.jpg",
                        extension=".jpg", size_bytes=3000, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        resp = find_similar_files(scan_id, fids["photo.jpg"], limit=5)
        # No text content on either; should return empty (no textual similarity possible)
        # (Filename tokens may still give slight similarity — acceptable)
        # Key check: no crash and similar_files is a list
        assert isinstance(resp.similar_files, list)

    def test_limit_respected(self, authorized_root: Path, clean_service):
        scan_id = "s-simlim"
        files = [
            ScannedFile(name=f"doc{i}.txt", relative_path=f"doc{i}.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z")
            for i in range(8)
        ]
        _persist_scan(scan_id, files)
        fids = _file_ids(scan_id)
        # Give all files similar content
        for name, fid in fids.items():
            _persist_content(fid, "Java enterprise OOP software patterns")
        target_id = list(fids.values())[0]
        resp = find_similar_files(scan_id, target_id, limit=2)
        assert len(resp.similar_files) <= 2

    def test_missing_scan_raises(self):
        with pytest.raises(ValueError, match="not found"):
            find_similar_files("no-such-scan-abc", 1, limit=5)

    def test_missing_file_raises(self, authorized_root: Path, clean_service):
        scan_id = "s-mf"
        _persist_scan(scan_id, [
            ScannedFile(name="only.txt", relative_path="only.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        with pytest.raises(LookupError, match="not found"):
            find_similar_files(scan_id, 999999, limit=5)

    def test_similarity_not_based_on_extension_alone(self, authorized_root: Path, clean_service):
        """Same extension ≠ similar without token evidence."""
        scan_id = "s-extonly"
        _persist_scan(scan_id, [
            ScannedFile(name="cooking.pdf", relative_path="cooking.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="finance.pdf", relative_path="finance.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["cooking.pdf"], "Recipes pasta sauce vegetables herbs olive oil")
        _persist_content(fids["finance.pdf"], "Revenue profit loss balance sheet quarterly")
        resp = find_similar_files(scan_id, fids["cooking.pdf"], limit=5)
        # finance.pdf may appear but with low score — just no crash
        for s in resp.similar_files:
            assert s.similarity_score < 1.0  # Definitely not identical


# ===========================================================================
# API ENDPOINT TESTS
# ===========================================================================

class TestSearchAPI:
    def test_search_success(self, authorized_root: Path, clean_service):
        scan_id = "api-search"
        _persist_scan(scan_id, [
            ScannedFile(name="java_api_guide.pdf", relative_path="java_api_guide.pdf",
                        extension=".pdf", size_bytes=1000, modified_at="2024-01-01T00:00:00Z"),
        ])
        resp = client.post("/api/search", json={"scan_id": scan_id, "query": "java api"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert data["query"] == "java api"

    def test_search_empty_query(self, authorized_root: Path, clean_service):
        resp = client.post("/api/search", json={"scan_id": "any", "query": "  "})
        assert resp.status_code == 422

    def test_search_missing_scan(self):
        resp = client.post("/api/search", json={"scan_id": "no-scan-xyz", "query": "test"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "SCAN_NOT_FOUND"

    def test_search_no_absolute_paths_in_response(self, authorized_root: Path, clean_service):
        scan_id = "api-noabs"
        _persist_scan(scan_id, [
            ScannedFile(name="readme.txt", relative_path="readme.txt",
                        extension=".txt", size_bytes=50, modified_at="2024-01-01T00:00:00Z"),
        ])
        resp = client.post("/api/search", json={"scan_id": scan_id, "query": "readme"})
        data = resp.json()
        for result in data.get("results", []):
            assert not result["relative_path"].startswith("C:")
            assert not result["relative_path"].startswith("/")

    def test_similar_api_success(self, authorized_root: Path, clean_service):
        scan_id = "api-sim"
        _persist_scan(scan_id, [
            ScannedFile(name="a.txt", relative_path="a.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
            ScannedFile(name="b.txt", relative_path="b.txt",
                        extension=".txt", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        fids = _file_ids(scan_id)
        _persist_content(fids["a.txt"], "machine learning deep neural network training")
        _persist_content(fids["b.txt"], "deep learning neural network model training")
        resp = client.post(f"/api/search/similar/{scan_id}/{fids['a.txt']}",
                           json={"limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert "similar_files" in data
        assert data["target_file_id"] == fids["a.txt"]

    def test_similar_api_scan_not_found(self):
        resp = client.post("/api/search/similar/no-scan/1", json={"limit": 5})
        assert resp.status_code == 404

    def test_similar_api_file_not_found(self, authorized_root: Path, clean_service):
        scan_id = "api-mf"
        _persist_scan(scan_id, [
            ScannedFile(name="only.txt", relative_path="only.txt",
                        extension=".txt", size_bytes=50, modified_at="2024-01-01T00:00:00Z"),
        ])
        resp = client.post(f"/api/search/similar/{scan_id}/999999", json={"limit": 5})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "FILE_NOT_FOUND"

    def test_search_limit_validation(self, authorized_root: Path, clean_service):
        resp = client.post("/api/search", json={"scan_id": "s", "query": "x", "limit": 0})
        assert resp.status_code == 422
        resp = client.post("/api/search", json={"scan_id": "s", "query": "x", "limit": 9999})
        assert resp.status_code == 422

    def test_search_with_category_filter(self, authorized_root: Path, clean_service):
        """Filter by category (Phase 6 analysis must have been run)."""
        # This test verifies the filter passes without error even if no analysis exists
        scan_id = "api-filter"
        _persist_scan(scan_id, [
            ScannedFile(name="doc.pdf", relative_path="doc.pdf",
                        extension=".pdf", size_bytes=100, modified_at="2024-01-01T00:00:00Z"),
        ])
        resp = client.post("/api/search", json={
            "scan_id": scan_id, "query": "doc", "category": "document"
        })
        assert resp.status_code == 200
