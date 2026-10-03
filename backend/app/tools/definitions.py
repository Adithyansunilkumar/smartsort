"""Phase 10 — Tool Definitions.

All SmartSort capabilities are registered here as typed ToolDefinitions.
Each definition wraps an existing Phase 2–9 service with:

  * a declared MutationPolicy (READ_ONLY or WRITE_DB)
  * an input_schema that documents required/optional parameters
  * a handler that delegates to the underlying service and returns ToolResult

No new service logic lives here — only the thin adapter layer.

Tool catalogue
--------------
FILESYSTEM
  * scan_directory          — run a fresh filesystem scan (WRITE_DB)
  * list_scan_files         — list files from a persisted scan (READ_ONLY)

HASHING
  * hash_files              — compute SHA-256 hashes for scan files (WRITE_DB)
  * find_duplicates         — find duplicate files in a scan (READ_ONLY)

EXTRACTION
  * extract_document_content — extract TXT/PDF/DOCX content (WRITE_DB)

ANALYSIS
  * analyze_files           — run deterministic file analysis (WRITE_DB)
  * get_analysis_summary    — return persisted analysis summary (READ_ONLY)

SEARCH
  * search_files            — ranked filename/content search (READ_ONLY)
  * find_similar_files      — TF-IDF cosine similarity (READ_ONLY)

ORGANIZATION
  * generate_plan           — produce an organization plan (WRITE_DB)
  * get_plan                — retrieve a persisted plan (READ_ONLY)

AGENT
  * start_agent_run         — start an orchestrated agent workflow (WRITE_DB)
  * get_agent_run           — retrieve agent run status (READ_ONLY)
  * cancel_agent_run        — cancel an in-progress agent run (WRITE_DB)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.tools.models import (
    MutationPolicy,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry factory
# ---------------------------------------------------------------------------


def build_registry() -> ToolRegistry:
    """Create and return a fully populated ToolRegistry.

    All definitions are registered in this single function.  Callers should
    store the returned registry as a singleton (e.g. on the FastAPI app
    state).
    """
    registry = ToolRegistry()

    # -- FILESYSTEM ----------------------------------------------------------
    registry.register(_scan_directory_tool())
    registry.register(_list_scan_files_tool())

    # -- HASHING -------------------------------------------------------------
    registry.register(_hash_files_tool())
    registry.register(_find_duplicates_tool())

    # -- EXTRACTION ----------------------------------------------------------
    registry.register(_extract_document_content_tool())

    # -- ANALYSIS ------------------------------------------------------------
    registry.register(_analyze_files_tool())
    registry.register(_get_analysis_summary_tool())

    # -- SEARCH --------------------------------------------------------------
    registry.register(_search_files_tool())
    registry.register(_find_similar_files_tool())

    # -- ORGANIZATION --------------------------------------------------------
    registry.register(_generate_plan_tool())
    registry.register(_get_plan_tool())

    # -- AGENT ---------------------------------------------------------------
    registry.register(_start_agent_run_tool())
    registry.register(_get_agent_run_tool())
    registry.register(_cancel_agent_run_tool())

    return registry


# ===========================================================================
# FILESYSTEM tools
# ===========================================================================


def _scan_directory_tool() -> ToolDefinition:
    """Wrap Phase 2 filesystem scanner."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.filesystem.scanner import run_scan
        from app.database.repositories import ScanRepository

        scan_result = run_scan(ctx.folder_service)
        ScanRepository.save_scan(scan_result)
        return ToolResult.ok(
            "scan_directory",
            {
                "scan_id": scan_result.scan_id,
                "file_count": scan_result.file_count,
                "directory_count": scan_result.directory_count,
                "status": scan_result.status.value,
                "scanned_at": scan_result.scanned_at,
            },
        )

    return ToolDefinition(
        name="scan_directory",
        description=(
            "Run a full recursive filesystem scan of the authorized root directory. "
            "Persists the result to the database and returns the scan_id and counts. "
            "Does not read file contents or compute hashes."
        ),
        category=ToolCategory.FILESYSTEM,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={},
        handler=handler,
    )


def _list_scan_files_tool() -> ToolDefinition:
    """Retrieve file records from a persisted scan."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.database.repositories import ScanRepository

        scan_id: str = inputs["scan_id"]
        files = ScanRepository.get_files_by_scan_id(scan_id)
        if files is None:
            return ToolResult.fail(
                "list_scan_files",
                "not_found",
                f"Scan '{scan_id}' not found.",
            )
        return ToolResult.ok(
            "list_scan_files",
            {"scan_id": scan_id, "file_count": len(files), "files": files},
        )

    return ToolDefinition(
        name="list_scan_files",
        description=(
            "Retrieve the list of files from a previously persisted scan. "
            "Returns file metadata (name, path, size, extension, mime type) "
            "without reading file contents."
        ),
        category=ToolCategory.FILESYSTEM,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to retrieve files for.",
                "required": True,
            }
        },
        handler=handler,
    )


# ===========================================================================
# HASHING tools
# ===========================================================================


def _hash_files_tool() -> ToolDefinition:
    """Wrap Phase 4 SHA-256 hashing."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.database.repositories import ScanRepository
        from app.filesystem.hasher import calculate_file_hash
        from app.database.connection import get_connection
        from app.security.paths import PathSecurityError

        scan_id: str = inputs["scan_id"]
        files = ScanRepository.get_files_by_scan_id(scan_id)
        if not files:
            return ToolResult.fail(
                "hash_files",
                "not_found",
                f"Scan '{scan_id}' not found or contains no files.",
            )

        hashed = 0
        skipped = 0
        for f in files:
            if f.get("hash"):
                skipped += 1
                continue
            try:
                canonical = ctx.folder_service.validate_path_in_root(
                    f["path"], allow_root=False
                )
                file_hash = calculate_file_hash(canonical)
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE files SET hash = ? WHERE id = ?",
                        (file_hash, f["id"]),
                    )
                hashed += 1
            except (PathSecurityError, OSError):
                skipped += 1

        return ToolResult.ok(
            "hash_files",
            {"scan_id": scan_id, "hashed": hashed, "skipped": skipped},
        )

    return ToolDefinition(
        name="hash_files",
        description=(
            "Compute SHA-256 hashes for all un-hashed files in a scan and "
            "persist them to the database. Skips files already hashed or "
            "inaccessible. Filesystem is not modified."
        ),
        category=ToolCategory.HASHING,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to hash files for.",
                "required": True,
            }
        },
        handler=handler,
    )


def _find_duplicates_tool() -> ToolDefinition:
    """Return duplicate file groups within a scan."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.database.connection import get_connection

        scan_id: str = inputs["scan_id"]
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT hash, COUNT(*) as cnt
                FROM files
                WHERE scan_id = ? AND hash IS NOT NULL
                GROUP BY hash
                HAVING COUNT(*) > 1
                """,
                (scan_id,),
            )
            dup_hashes = {row["hash"]: row["cnt"] for row in cursor}

        if not dup_hashes:
            return ToolResult.ok(
                "find_duplicates",
                {"scan_id": scan_id, "duplicate_groups": []},
            )

        groups = []
        with get_connection() as conn:
            for h in dup_hashes:
                cursor = conn.execute(
                    "SELECT id, path, name, size FROM files WHERE scan_id = ? AND hash = ?",
                    (scan_id, h),
                )
                members = [dict(r) for r in cursor]
                groups.append({"hash": h, "count": len(members), "files": members})

        return ToolResult.ok(
            "find_duplicates",
            {
                "scan_id": scan_id,
                "duplicate_groups": sorted(groups, key=lambda g: g["hash"]),
            },
        )

    return ToolDefinition(
        name="find_duplicates",
        description=(
            "Identify groups of files with the same SHA-256 hash within a scan. "
            "Requires hashes to have been computed by hash_files first. "
            "Read-only — does not modify the database or filesystem."
        ),
        category=ToolCategory.HASHING,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to find duplicates in.",
                "required": True,
            }
        },
        handler=handler,
    )


# ===========================================================================
# EXTRACTION tools
# ===========================================================================


def _extract_document_content_tool() -> ToolDefinition:
    """Wrap Phase 5 document content extraction."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.services.extraction import extract_scan_documents

        scan_id: str = inputs["scan_id"]
        try:
            result = extract_scan_documents(scan_id, ctx.folder_service)
            return ToolResult.ok(
                "extract_document_content",
                {
                    "scan_id": result.scan_id,
                    "total_processed": result.total_processed,
                    "successful": result.successful,
                    "empty": result.empty,
                    "skipped": result.unsupported,
                    "failed": result.failed,
                },
            )
        except ValueError as exc:
            return ToolResult.fail(
                "extract_document_content", "not_found", str(exc)
            )

    return ToolDefinition(
        name="extract_document_content",
        description=(
            "Extract text content from TXT, PDF, and DOCX files in a scan and "
            "persist results to the database. Uses freshness detection to skip "
            "unchanged files. Filesystem is not modified."
        ),
        category=ToolCategory.EXTRACTION,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to extract content for.",
                "required": True,
            }
        },
        handler=handler,
    )


# ===========================================================================
# ANALYSIS tools
# ===========================================================================


def _analyze_files_tool() -> ToolDefinition:
    """Wrap Phase 6 deterministic file analysis."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.analysis.service import analyze_scan_files

        scan_id: str = inputs["scan_id"]
        try:
            summary = analyze_scan_files(scan_id, ctx.folder_service)
            return ToolResult.ok(
                "analyze_files",
                {
                    "scan_id": scan_id,
                    "total_analyzed": summary.total_analyzed,
                    "categories": summary.categories,
                    "age_buckets": summary.age_buckets,
                    "size_buckets": summary.size_buckets,
                    "duplicates_found": summary.duplicates_found,
                    "content_analyzed": summary.content_analyzed,
                    "temp_or_copy_files": summary.temp_or_copy_files,
                    "total_size_bytes": summary.total_size_bytes,
                },
            )
        except ValueError as exc:
            return ToolResult.fail("analyze_files", "not_found", str(exc))

    return ToolDefinition(
        name="analyze_files",
        description=(
            "Run deterministic file analysis on a scan, computing category, "
            "age, size bucket, filename signals, and content metrics. "
            "Persists results and uses freshness detection to skip unchanged files."
        ),
        category=ToolCategory.ANALYSIS,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to analyze.",
                "required": True,
            }
        },
        handler=handler,
    )


def _get_analysis_summary_tool() -> ToolDefinition:
    """Return persisted analysis summary without recomputing."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.database.repositories import AnalysisRepository, ScanRepository

        scan_id: str = inputs["scan_id"]
        files = ScanRepository.get_files_by_scan_id(scan_id)
        if files is None:
            return ToolResult.fail(
                "get_analysis_summary",
                "not_found",
                f"Scan '{scan_id}' not found.",
            )
        results = AnalysisRepository.get_analysis_results_for_scan(scan_id)
        categories: Dict[str, int] = {}
        age_buckets: Dict[str, int] = {}
        size_buckets: Dict[str, int] = {}
        for r in results:
            cat = r.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
            ab = r.get("age_bucket", "unknown")
            age_buckets[ab] = age_buckets.get(ab, 0) + 1
            sb = r.get("size_bucket", "unknown")
            size_buckets[sb] = size_buckets.get(sb, 0) + 1

        return ToolResult.ok(
            "get_analysis_summary",
            {
                "scan_id": scan_id,
                "total_analyzed": len(results),
                "categories": categories,
                "age_buckets": age_buckets,
                "size_buckets": size_buckets,
            },
        )

    return ToolDefinition(
        name="get_analysis_summary",
        description=(
            "Return an aggregated summary of persisted file-analysis results "
            "for a scan without recomputing anything. Read-only."
        ),
        category=ToolCategory.ANALYSIS,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to summarize.",
                "required": True,
            }
        },
        handler=handler,
    )


# ===========================================================================
# SEARCH tools
# ===========================================================================


def _search_files_tool() -> ToolDefinition:
    """Wrap Phase 7 ranked filename/content search."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.search.service import search_scan
        from app.search.models import SearchRequest

        scan_id: str = inputs["scan_id"]
        query: str = inputs["query"]
        limit: int = int(inputs.get("limit", 20))

        request = SearchRequest(
            scan_id=scan_id,
            query=query,
            limit=limit,
        )
        try:
            response = search_scan(request)
            return ToolResult.ok(
                "search_files",
                {
                    "scan_id": response.scan_id,
                    "query": response.query,
                    "total_results": response.total_results,
                    "results": [r.dict() for r in response.results],
                },
            )
        except ValueError as exc:
            return ToolResult.fail("search_files", "not_found", str(exc))

    return ToolDefinition(
        name="search_files",
        description=(
            "Search files in a scan by filename and content using TF-IDF cosine "
            "similarity and lexical scoring. Returns ranked results with "
            "relevance, similarity, and lexical scores."
        ),
        category=ToolCategory.SEARCH,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to search within.",
                "required": True,
            },
            "query": {
                "type": "string",
                "description": "The search query string.",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default 20).",
                "required": False,
            },
        },
        handler=handler,
    )


def _find_similar_files_tool() -> ToolDefinition:
    """Wrap Phase 7 TF-IDF cosine similarity."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.search.service import find_similar_files

        scan_id: str = inputs["scan_id"]
        file_id: int = int(inputs["file_id"])
        limit: int = int(inputs.get("limit", 10))

        try:
            response = find_similar_files(scan_id, file_id, limit)
            return ToolResult.ok(
                "find_similar_files",
                {
                    "scan_id": response.scan_id,
                    "target_file_id": response.target_file_id,
                    "target_name": response.target_name,
                    "similar_files": [r.dict() for r in response.similar_files],
                },
            )
        except ValueError as exc:
            return ToolResult.fail("find_similar_files", "not_found", str(exc))
        except LookupError as exc:
            return ToolResult.fail("find_similar_files", "not_found", str(exc))

    return ToolDefinition(
        name="find_similar_files",
        description=(
            "Find files most similar to a target file using TF-IDF cosine "
            "similarity over filename, path, and document content tokens. "
            "Read-only — operates entirely on persisted data."
        ),
        category=ToolCategory.SEARCH,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID containing the target file.",
                "required": True,
            },
            "file_id": {
                "type": "integer",
                "description": "The numeric ID of the target file.",
                "required": True,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of similar files to return (default 10).",
                "required": False,
            },
        },
        handler=handler,
    )


# ===========================================================================
# ORGANIZATION tools
# ===========================================================================


def _generate_plan_tool() -> ToolDefinition:
    """Wrap Phase 8 organization plan generation."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.organization.service import generate_organization_plan

        scan_id: str = inputs["scan_id"]
        objective: str = inputs.get("objective", "")

        try:
            plan = generate_organization_plan(
                scan_id=scan_id,
                folder_service=ctx.folder_service,
                objective=objective or None,
            )
            return ToolResult.ok(
                "generate_plan",
                {
                    "plan_id": plan.plan_id,
                    "scan_id": plan.scan_id,
                    "objective": plan.objective,
                    "status": plan.status,
                    "total_operations": plan.total_operations,
                    "created_at": plan.created_at,
                },
            )
        except ValueError as exc:
            return ToolResult.fail("generate_plan", "not_found", str(exc))

    return ToolDefinition(
        name="generate_plan",
        description=(
            "Generate a deterministic, read-only organization plan for a scan. "
            "Proposes file moves, renames, folder creation, and duplicate/large/"
            "old-file warnings. Does not execute any operations."
        ),
        category=ToolCategory.ORGANIZATION,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "scan_id": {
                "type": "string",
                "description": "The scan ID to plan organization for.",
                "required": True,
            },
            "objective": {
                "type": "string",
                "description": "Optional human objective (e.g. 'organize by category').",
                "required": False,
            },
        },
        handler=handler,
    )


def _get_plan_tool() -> ToolDefinition:
    """Return a persisted organization plan by ID."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.database.repositories import OrganizationRepository

        plan_id: str = inputs["plan_id"]
        plan = OrganizationRepository.get_plan(plan_id)
        if not plan:
            return ToolResult.fail(
                "get_plan",
                "not_found",
                f"Organization plan '{plan_id}' not found.",
            )
        # operations are embedded in the plan dict by OrganizationRepository.get_plan
        ops = plan.get("operations", [])
        return ToolResult.ok(
            "get_plan",
            {
                "plan_id": plan["plan_id"],
                "scan_id": plan["scan_id"],
                "objective": plan.get("objective"),
                "warnings": plan.get("warnings", []),
                "protected_conflicts": plan.get("protected_conflicts", []),
                "created_at": plan["created_at"],
                "operations": ops,
                "total_operations": len(ops),
            },
        )

    return ToolDefinition(
        name="get_plan",
        description=(
            "Retrieve a previously generated organization plan and its operations "
            "from the database. Read-only."
        ),
        category=ToolCategory.ORGANIZATION,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "plan_id": {
                "type": "string",
                "description": "The plan ID to retrieve.",
                "required": True,
            }
        },
        handler=handler,
    )


# ===========================================================================
# AGENT tools
# ===========================================================================


def _start_agent_run_tool() -> ToolDefinition:
    """Wrap Phase 9 agent run creation."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.agent.controller import AgentController

        objective: str = inputs["objective"]
        scan_id: str = inputs.get("scan_id", "") or None

        controller = AgentController(ctx.folder_service)
        try:
            run = controller.start_run(objective=objective, scan_id=scan_id)
            return ToolResult.ok(
                "start_agent_run",
                {
                    "run_id": run.run_id,
                    "state": run.state.value,
                    "plan_id": run.plan_id,
                    "scan_id": run.scan_id,
                    "progress": run.progress,
                    "current_step": run.current_step,
                },
            )
        except ValueError as exc:
            return ToolResult.fail("start_agent_run", "validation", str(exc))

    return ToolDefinition(
        name="start_agent_run",
        description=(
            "Start a full agent workflow run: scan → analyze → plan → "
            "WAITING_FOR_APPROVAL. Returns the final run state and plan_id. "
            "No files are modified. Database is updated with the run record."
        ),
        category=ToolCategory.AGENT,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "objective": {
                "type": "string",
                "description": "Human-readable organization objective.",
                "required": True,
            },
            "scan_id": {
                "type": "string",
                "description": "Optional existing scan ID to reuse instead of rescanning.",
                "required": False,
            },
        },
        handler=handler,
    )


def _get_agent_run_tool() -> ToolDefinition:
    """Return agent run status."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.agent.repository import AgentRepository

        run_id: str = inputs["run_id"]
        run = AgentRepository.get_run(run_id)
        if not run:
            return ToolResult.fail(
                "get_agent_run",
                "not_found",
                f"Agent run '{run_id}' not found.",
            )
        return ToolResult.ok(
            "get_agent_run",
            {
                "run_id": run.run_id,
                "state": run.state.value,
                "plan_id": run.plan_id,
                "scan_id": run.scan_id,
                "progress": run.progress,
                "current_step": run.current_step,
                "error": run.error,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
            },
        )

    return ToolDefinition(
        name="get_agent_run",
        description=(
            "Retrieve the current state, progress, and plan_id of a previously "
            "started agent run. Read-only."
        ),
        category=ToolCategory.AGENT,
        mutation_policy=MutationPolicy.READ_ONLY,
        input_schema={
            "run_id": {
                "type": "string",
                "description": "The agent run ID to retrieve.",
                "required": True,
            }
        },
        handler=handler,
    )


def _cancel_agent_run_tool() -> ToolDefinition:
    """Cancel an active agent run."""

    def handler(inputs: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        from app.agent.controller import AgentController
        from app.agent.state import InvalidStateTransitionError

        run_id: str = inputs["run_id"]
        controller = AgentController(ctx.folder_service)
        try:
            run = controller.cancel_run(run_id)
            return ToolResult.ok(
                "cancel_agent_run",
                {
                    "run_id": run.run_id,
                    "state": run.state.value,
                    "current_step": run.current_step,
                },
            )
        except ValueError as exc:
            return ToolResult.fail("cancel_agent_run", "not_found", str(exc))
        except InvalidStateTransitionError as exc:
            return ToolResult.fail("cancel_agent_run", "validation", str(exc))

    return ToolDefinition(
        name="cancel_agent_run",
        description=(
            "Cancel an active or waiting agent run. Idempotent — safe to call "
            "on an already-cancelled run. Does not undo any database writes "
            "already performed."
        ),
        category=ToolCategory.AGENT,
        mutation_policy=MutationPolicy.WRITE_DB,
        input_schema={
            "run_id": {
                "type": "string",
                "description": "The agent run ID to cancel.",
                "required": True,
            }
        },
        handler=handler,
    )
