"""Phase 10 — Tool System API endpoints.

Endpoints
---------
GET  /api/tools                   — list all registered tools (metadata only)
GET  /api/tools/{tool_name}       — get a single tool's metadata
POST /api/tools/{tool_name}/invoke — invoke a tool by name with inputs

The registry is built once at application startup and stored on ``app.state``.
If the registry is not yet initialised, endpoints return HTTP 503.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.folder_access import FolderAccessService, get_folder_access_service
from app.tools.models import (
    MutationPolicy,
    ToolCategory,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])


# ---------------------------------------------------------------------------
# Response models (API-visible, no callable)
# ---------------------------------------------------------------------------


class ToolMetadataResponse(BaseModel):
    """Public metadata for a registered tool (handler excluded)."""

    name: str
    description: str
    category: ToolCategory
    mutation_policy: MutationPolicy
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class ToolListResponse(BaseModel):
    tool_count: int
    tools: List[ToolMetadataResponse]


class ToolInvokeRequest(BaseModel):
    """Inputs for a tool invocation call."""

    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs matching the tool's input_schema.",
    )


class ToolInvokeResponse(BaseModel):
    """API response wrapping a ToolResult."""

    tool_name: str
    success: bool
    data: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_registry(request: Request):
    """Retrieve the ToolRegistry from FastAPI app state."""
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "REGISTRY_NOT_INITIALIZED",
                "message": "Tool registry is not available. The service may be starting up.",
            },
        )
    return registry


def _tool_to_metadata(tool: ToolDefinition) -> ToolMetadataResponse:
    return ToolMetadataResponse(
        name=tool.name,
        description=tool.description,
        category=tool.category,
        mutation_policy=tool.mutation_policy,
        input_schema=tool.input_schema,
    )


def _result_to_response(result: ToolResult) -> ToolInvokeResponse:
    error_payload = None
    if result.error:
        error_payload = result.error.dict()
    return ToolInvokeResponse(
        tool_name=result.tool_name,
        success=result.success,
        data=result.data,
        error=error_payload,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ToolListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all registered tools",
    description=(
        "Returns metadata for every tool registered in the SmartSort Tool Registry. "
        "Tool handlers (callables) are not included in the response."
    ),
)
def list_tools(request: Request) -> ToolListResponse:
    """List all registered tools."""
    registry = _get_registry(request)
    tools = registry.list_tools()
    return ToolListResponse(
        tool_count=len(tools),
        tools=[_tool_to_metadata(t) for t in tools],
    )


@router.get(
    "/{tool_name}",
    response_model=ToolMetadataResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single tool's metadata",
    description="Returns metadata for the named tool, or 404 if not found.",
)
def get_tool(tool_name: str, request: Request) -> ToolMetadataResponse:
    """Retrieve a single tool by name."""
    registry = _get_registry(request)
    tool = registry.get_tool(tool_name)
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "TOOL_NOT_FOUND",
                "message": f"Tool '{tool_name}' is not registered.",
            },
        )
    return _tool_to_metadata(tool)


@router.post(
    "/{tool_name}/invoke",
    response_model=ToolInvokeResponse,
    status_code=status.HTTP_200_OK,
    summary="Invoke a tool by name",
    description=(
        "Invokes the named tool with the supplied inputs. "
        "The response always contains a success flag and either data or an error. "
        "HTTP 200 is returned even when the tool itself reports a failure — "
        "inspect the 'success' field."
    ),
)
def invoke_tool(
    tool_name: str,
    body: ToolInvokeRequest,
    request: Request,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> ToolInvokeResponse:
    """Invoke a registered tool with validated inputs."""
    registry = _get_registry(request)

    context = ToolContext(folder_service=folder_service)
    result = registry.invoke(tool_name, body.inputs, context)
    return _result_to_response(result)
