"""Phase 10 — Tool System tests.

Test coverage:
1. ToolRegistry registration and lookup
2. Duplicate registration rejection
3. Unknown tool invocation → typed error result
4. Missing required input → typed validation error
5. Handler exception isolation → typed execution error
6. MutationPolicy invariants (no FILESYSTEM_WRITE tools)
7. All 15 tools are registered with correct categories
8. API: GET /api/tools returns all tools
9. API: GET /api/tools/{name} returns metadata / 404
10. API: POST /api/tools/{name}/invoke with valid inputs
11. API: POST /api/tools/{name}/invoke with unknown tool
12. API: POST /api/tools/{name}/invoke with missing required input
13. Read-only tool invocation does not mutate the filesystem
"""

import os
import shutil
import tempfile
import hashlib
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.tools.models import (
    MutationPolicy,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from app.tools.registry import DuplicateToolError, ToolRegistry
from app.tools.definitions import build_registry
from app.services.folder_access import FolderAccessService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> ToolRegistry:
    """Return a fresh, fully populated registry."""
    return build_registry()


@pytest.fixture()
def empty_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture()
def tmp_folder(tmp_path: Path) -> Path:
    """Temporary directory with a few test files."""
    (tmp_path / "report.txt").write_text("Hello World from SmartSort Phase 10.")
    (tmp_path / "notes.txt").write_text("These are test notes for Phase 10.")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "data.txt").write_text("Nested document content.")
    return tmp_path


@pytest.fixture()
def folder_service(tmp_folder: Path) -> FolderAccessService:
    svc = FolderAccessService()
    svc.set_authorized_root(tmp_folder)  # Pass Path, not str
    return svc


@pytest.fixture()
def tool_context(folder_service: FolderAccessService) -> ToolContext:
    return ToolContext(folder_service=folder_service)


@pytest.fixture()
def app_client(tmp_folder: Path):
    """TestClient with the full app and a configured authorized root."""
    from app.main import app
    from app.services.folder_access import get_folder_access_service

    svc = FolderAccessService()
    svc.set_authorized_root(tmp_folder)  # Pass Path, not str

    app.dependency_overrides[get_folder_access_service] = lambda: svc

    # Ensure tool_registry is set on app state (simulates lifespan startup)
    from app.tools.definitions import build_registry
    app.state.tool_registry = build_registry()

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Registry registration and lookup
# ---------------------------------------------------------------------------


class TestRegistryBasics:
    def test_register_and_lookup(self, empty_registry: ToolRegistry):
        """A registered tool can be retrieved by name."""
        def noop(inputs, ctx):
            return ToolResult.ok("test_tool", {})

        tool = ToolDefinition(
            name="test_tool",
            description="Test tool",
            category=ToolCategory.FILESYSTEM,
            mutation_policy=MutationPolicy.READ_ONLY,
            input_schema={},
            handler=noop,
        )
        empty_registry.register(tool)
        assert "test_tool" in empty_registry
        retrieved = empty_registry.get_tool("test_tool")
        assert retrieved is not None
        assert retrieved.name == "test_tool"

    def test_list_tools_sorted(self, empty_registry: ToolRegistry):
        """list_tools returns tools in alphabetical order."""
        for name in ["zebra", "alpha", "mango"]:
            def noop(inputs, ctx, _n=name):
                return ToolResult.ok(_n, {})

            empty_registry.register(
                ToolDefinition(
                    name=name,
                    description="",
                    category=ToolCategory.FILESYSTEM,
                    mutation_policy=MutationPolicy.READ_ONLY,
                    input_schema={},
                    handler=noop,
                )
            )
        names = [t.name for t in empty_registry.list_tools()]
        assert names == sorted(names)

    def test_get_unknown_tool_returns_none(self, empty_registry: ToolRegistry):
        assert empty_registry.get_tool("nonexistent") is None

    def test_len(self, registry: ToolRegistry):
        assert len(registry) == 14  # 2+2+1+2+2+2+3 = 14 Phase 10 tools

    def test_tool_names(self, registry: ToolRegistry):
        names = registry.tool_names()
        assert "scan_directory" in names
        assert "find_duplicates" in names
        assert "search_files" in names
        assert "find_similar_files" in names
        assert "generate_plan" in names
        assert "start_agent_run" in names


# ---------------------------------------------------------------------------
# 2. Duplicate registration rejection
# ---------------------------------------------------------------------------


class TestDuplicateRegistration:
    def test_duplicate_raises(self, empty_registry: ToolRegistry):
        def noop(inputs, ctx):
            return ToolResult.ok("dup", {})

        tool = ToolDefinition(
            name="dup",
            description="",
            category=ToolCategory.FILESYSTEM,
            mutation_policy=MutationPolicy.READ_ONLY,
            input_schema={},
            handler=noop,
        )
        empty_registry.register(tool)
        with pytest.raises(DuplicateToolError):
            empty_registry.register(tool)


# ---------------------------------------------------------------------------
# 3. Unknown tool invocation → typed error
# ---------------------------------------------------------------------------


class TestUnknownToolInvocation:
    def test_unknown_tool_returns_fail(
        self, empty_registry: ToolRegistry, tool_context: ToolContext
    ):
        result = empty_registry.invoke("nonexistent_tool", {}, tool_context)
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == "not_found"
        assert "nonexistent_tool" in result.error.message


# ---------------------------------------------------------------------------
# 4. Missing required input → validation error
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_missing_required_input(
        self, registry: ToolRegistry, tool_context: ToolContext
    ):
        # search_files requires scan_id and query
        result = registry.invoke("search_files", {"scan_id": "x"}, tool_context)
        assert not result.success
        assert result.error is not None
        assert result.error.error_type == "validation"
        assert "query" in result.error.message

    def test_missing_scan_id(
        self, registry: ToolRegistry, tool_context: ToolContext
    ):
        result = registry.invoke("list_scan_files", {}, tool_context)
        assert not result.success
        assert result.error.error_type == "validation"


# ---------------------------------------------------------------------------
# 5. Handler exception isolation
# ---------------------------------------------------------------------------


class TestHandlerExceptionIsolation:
    def test_handler_exception_wrapped(
        self, empty_registry: ToolRegistry, tool_context: ToolContext
    ):
        def boom(inputs, ctx):
            raise RuntimeError("Catastrophic internal failure")

        empty_registry.register(
            ToolDefinition(
                name="boom_tool",
                description="",
                category=ToolCategory.FILESYSTEM,
                mutation_policy=MutationPolicy.READ_ONLY,
                input_schema={},
                handler=boom,
            )
        )
        result = empty_registry.invoke("boom_tool", {}, tool_context)
        assert not result.success
        assert result.error.error_type == "execution"
        assert "Catastrophic" in result.error.message

    def test_handler_wrong_return_type(
        self, empty_registry: ToolRegistry, tool_context: ToolContext
    ):
        """A handler returning a non-ToolResult triggers an execution error."""
        def bad(inputs, ctx):
            return {"oops": True}  # Not a ToolResult

        empty_registry.register(
            ToolDefinition(
                name="bad_return_tool",
                description="",
                category=ToolCategory.FILESYSTEM,
                mutation_policy=MutationPolicy.READ_ONLY,
                input_schema={},
                handler=bad,
            )
        )
        result = empty_registry.invoke("bad_return_tool", {}, tool_context)
        assert not result.success
        assert result.error.error_type == "execution"


# ---------------------------------------------------------------------------
# 6. MutationPolicy invariants
# ---------------------------------------------------------------------------


class TestMutationPolicy:
    def test_no_filesystem_write_policy_exists(self, registry: ToolRegistry):
        """No Phase 10 tool should have a mutation policy beyond WRITE_DB."""
        valid_policies = {MutationPolicy.READ_ONLY, MutationPolicy.WRITE_DB}
        for tool in registry.list_tools():
            assert tool.mutation_policy in valid_policies, (
                f"Tool '{tool.name}' has unexpected mutation_policy: {tool.mutation_policy}"
            )

    def test_read_only_tools(self, registry: ToolRegistry):
        """Verify specific tools are declared READ_ONLY."""
        read_only_tools = {
            "list_scan_files",
            "find_duplicates",
            "get_analysis_summary",
            "search_files",
            "find_similar_files",
            "get_plan",
            "get_agent_run",
        }
        for name in read_only_tools:
            tool = registry.get_tool(name)
            assert tool is not None, f"Tool '{name}' not found"
            assert tool.mutation_policy == MutationPolicy.READ_ONLY, (
                f"Tool '{name}' should be READ_ONLY"
            )

    def test_write_db_tools(self, registry: ToolRegistry):
        """Verify specific tools are declared WRITE_DB."""
        write_db_tools = {
            "scan_directory",
            "hash_files",
            "extract_document_content",
            "analyze_files",
            "generate_plan",
            "start_agent_run",
            "cancel_agent_run",
        }
        for name in write_db_tools:
            tool = registry.get_tool(name)
            assert tool is not None, f"Tool '{name}' not found"
            assert tool.mutation_policy == MutationPolicy.WRITE_DB, (
                f"Tool '{name}' should be WRITE_DB"
            )


# ---------------------------------------------------------------------------
# 7. Tool categories
# ---------------------------------------------------------------------------


class TestToolCategories:
    def test_tool_categories(self, registry: ToolRegistry):
        expected = {
            "scan_directory": ToolCategory.FILESYSTEM,
            "list_scan_files": ToolCategory.FILESYSTEM,
            "hash_files": ToolCategory.HASHING,
            "find_duplicates": ToolCategory.HASHING,
            "extract_document_content": ToolCategory.EXTRACTION,
            "analyze_files": ToolCategory.ANALYSIS,
            "get_analysis_summary": ToolCategory.ANALYSIS,
            "search_files": ToolCategory.SEARCH,
            "find_similar_files": ToolCategory.SEARCH,
            "generate_plan": ToolCategory.ORGANIZATION,
            "get_plan": ToolCategory.ORGANIZATION,
            "start_agent_run": ToolCategory.AGENT,
            "get_agent_run": ToolCategory.AGENT,
            "cancel_agent_run": ToolCategory.AGENT,
        }
        for name, expected_cat in expected.items():
            tool = registry.get_tool(name)
            assert tool is not None, f"Tool '{name}' not registered"
            assert tool.category == expected_cat, (
                f"Tool '{name}': expected {expected_cat}, got {tool.category}"
            )


# ---------------------------------------------------------------------------
# 8–12. API tests
# ---------------------------------------------------------------------------


class TestToolsAPI:
    def test_list_tools_returns_all(self, app_client):
        resp = app_client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_count"] == 14
        names = {t["name"] for t in data["tools"]}
        assert "scan_directory" in names
        assert "search_files" in names
        assert "start_agent_run" in names

    def test_get_tool_found(self, app_client):
        resp = app_client.get("/api/tools/scan_directory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "scan_directory"
        assert data["category"] == "filesystem"
        assert data["mutation_policy"] == "write_db"

    def test_get_tool_not_found(self, app_client):
        resp = app_client.get("/api/tools/nonexistent_tool_xyz")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "TOOL_NOT_FOUND"

    def test_invoke_scan_directory(self, app_client):
        """scan_directory should succeed and return a scan_id."""
        resp = app_client.post("/api/tools/scan_directory/invoke", json={"inputs": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "scan_id" in data["data"]
        assert data["data"]["file_count"] >= 3

    def test_invoke_unknown_tool(self, app_client):
        """Invoking an unknown tool returns HTTP 200 with success=false."""
        resp = app_client.post("/api/tools/no_such_tool/invoke", json={"inputs": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["error_type"] == "not_found"

    def test_invoke_missing_required_input(self, app_client):
        """Invoking search_files without 'query' returns a validation error."""
        resp = app_client.post(
            "/api/tools/search_files/invoke",
            json={"inputs": {"scan_id": "fake_id"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["error_type"] == "validation"

    def test_invoke_list_scan_files_after_scan(self, app_client):
        """list_scan_files returns files from a completed scan."""
        # First run a scan
        scan_resp = app_client.post("/api/tools/scan_directory/invoke", json={"inputs": {}})
        assert scan_resp.json()["success"] is True
        scan_id = scan_resp.json()["data"]["scan_id"]

        # Now list files
        list_resp = app_client.post(
            "/api/tools/list_scan_files/invoke",
            json={"inputs": {"scan_id": scan_id}},
        )
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["success"] is True
        assert data["data"]["file_count"] >= 3

    def test_invoke_analyze_files_after_scan(self, app_client):
        """analyze_files runs deterministic analysis and returns summary."""
        scan_resp = app_client.post("/api/tools/scan_directory/invoke", json={"inputs": {}})
        scan_id = scan_resp.json()["data"]["scan_id"]

        analyze_resp = app_client.post(
            "/api/tools/analyze_files/invoke",
            json={"inputs": {"scan_id": scan_id}},
        )
        assert analyze_resp.status_code == 200
        data = analyze_resp.json()
        assert data["success"] is True
        assert data["data"]["total_analyzed"] >= 3

    def test_invoke_search_files_after_scan_and_analysis(self, app_client):
        """search_files returns ranked results for a query."""
        scan_resp = app_client.post("/api/tools/scan_directory/invoke", json={"inputs": {}})
        scan_id = scan_resp.json()["data"]["scan_id"]
        app_client.post(
            "/api/tools/analyze_files/invoke",
            json={"inputs": {"scan_id": scan_id}},
        )

        search_resp = app_client.post(
            "/api/tools/search_files/invoke",
            json={"inputs": {"scan_id": scan_id, "query": "notes"}},
        )
        assert search_resp.status_code == 200
        data = search_resp.json()
        assert data["success"] is True
        assert isinstance(data["data"]["results"], list)

    def test_get_plan_not_found(self, app_client):
        """get_plan returns a typed error for unknown plan IDs."""
        resp = app_client.post(
            "/api/tools/get_plan/invoke",
            json={"inputs": {"plan_id": "plan_nonexistent_xyz"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"]["error_type"] == "not_found"


# ---------------------------------------------------------------------------
# 13. Mutation safety — read-only tools must not mutate the filesystem
# ---------------------------------------------------------------------------


class TestMutationSafety:
    def test_read_only_tools_do_not_mutate_filesystem(
        self, registry: ToolRegistry, folder_service: FolderAccessService, tmp_folder: Path
    ):
        """Invoke all READ_ONLY tools and verify zero source-file changes.

        The snapshot deliberately excludes SQLite .db files because WRITE_DB
        tools legitimately write analysis data to the database.  The invariant
        we are verifying is that *source files* (txt, pdf, docx, etc.) are
        never modified.
        """
        def snapshot(root: Path) -> Dict[str, Any]:
            """Hash every non-DB file in the directory tree."""
            result = {}
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix.lower() != ".db":
                    content_hash = hashlib.sha256(p.read_bytes()).hexdigest()
                    result[str(p)] = {
                        "size": p.stat().st_size,
                        "hash": content_hash,
                    }
            return result

        before = snapshot(tmp_folder)
        context = ToolContext(folder_service=folder_service)

        # First run scan and analysis so READ_ONLY tools have data to read
        scan_result = registry.invoke("scan_directory", {}, context)
        assert scan_result.success, f"scan_directory failed: {scan_result.error}"
        scan_id = scan_result.data["scan_id"]

        analyze_result = registry.invoke("analyze_files", {"scan_id": scan_id}, context)
        assert analyze_result.success, f"analyze_files failed: {analyze_result.error}"

        # Invoke all READ_ONLY tools
        read_only_tools = [
            t for t in registry.list_tools()
            if t.mutation_policy == MutationPolicy.READ_ONLY
        ]

        for tool in read_only_tools:
            # Build minimal inputs for each tool
            if tool.name == "list_scan_files":
                inputs = {"scan_id": scan_id}
            elif tool.name == "get_analysis_summary":
                inputs = {"scan_id": scan_id}
            elif tool.name == "search_files":
                inputs = {"scan_id": scan_id, "query": "test"}
            elif tool.name == "find_similar_files":
                from app.database.repositories import ScanRepository
                files = ScanRepository.get_files_by_scan_id(scan_id)
                if not files:
                    continue
                inputs = {"scan_id": scan_id, "file_id": files[0]["id"]}
            elif tool.name == "find_duplicates":
                inputs = {"scan_id": scan_id}
            elif tool.name == "get_plan":
                inputs = {"plan_id": "plan_does_not_exist_abc"}
            elif tool.name == "get_agent_run":
                inputs = {"run_id": "run_does_not_exist_abc"}
            else:
                inputs = {}

            registry.invoke(tool.name, inputs, context)

        # Verify source files are exactly the same
        after = snapshot(tmp_folder)
        assert before == after, (
            "READ_ONLY tool invocations must not modify any source file. "
            f"Differences detected in: {set(after.keys()) - set(before.keys())}"
        )

