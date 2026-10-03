"""Phase 10 — Tool System typed models.

Defines all shared data types used by ToolRegistry and tool definitions.
These models are Pydantic-based for validation and serialization, and plain
dataclasses/enums for internal use where serialization is not needed.
"""

from __future__ import annotations

import enum
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ToolCategory(str, enum.Enum):
    """Logical grouping for tool capabilities."""

    FILESYSTEM = "filesystem"
    """Tools that discover or read filesystem metadata."""

    HASHING = "hashing"
    """Tools that compute or compare file hashes."""

    EXTRACTION = "extraction"
    """Tools that extract text content from documents."""

    ANALYSIS = "analysis"
    """Tools that compute deterministic file-analysis signals."""

    SEARCH = "search"
    """Tools that query or rank files by content/filename similarity."""

    ORGANIZATION = "organization"
    """Tools that generate read-only organization proposals."""

    AGENT = "agent"
    """Tools that control the Agent Controller workflow."""


class MutationPolicy(str, enum.Enum):
    """Declares whether a tool may mutate persisted state or the filesystem.

    Filesystem mutation is **never** permitted in Phase 10.
    READ_ONLY tools neither write to the filesystem nor to the database.
    WRITE_DB tools may write to the SQLite database (e.g. persisting scan or
    analysis results) but never touch the underlying file tree.
    """

    READ_ONLY = "read_only"
    """Tool reads data only; no database writes, no filesystem writes."""

    WRITE_DB = "write_db"
    """Tool may write to the SmartSort database; filesystem is untouched."""


# ---------------------------------------------------------------------------
# Tool Context
# ---------------------------------------------------------------------------


class ToolContext(BaseModel):
    """Runtime context supplied to every tool invocation.

    The context carries services and session identifiers that tools need
    without exposing them through the tool input payload.  It is *never*
    serialised over the API; tools receive it from the registry at invocation
    time.
    """

    folder_service: Any = Field(
        ...,
        description="FolderAccessService instance providing the security boundary.",
    )

    class Config:
        arbitrary_types_allowed = True


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------


class ToolDefinition(BaseModel):
    """Metadata and callable for a single registered tool.

    Every SmartSort capability is wrapped in a ToolDefinition so that the
    Agent Controller (and, in future phases, an LLM-based planner) can
    discover, validate, and invoke tools through a uniform interface.
    """

    name: str = Field(
        ...,
        description="Unique machine-readable tool name (snake_case).",
    )
    description: str = Field(
        ...,
        description="Human-readable description of what the tool does.",
    )
    category: ToolCategory = Field(
        ...,
        description="Logical grouping for the tool.",
    )
    mutation_policy: MutationPolicy = Field(
        ...,
        description="Declares whether the tool may write to the database or filesystem.",
    )
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON-Schema-style description of accepted input parameters. "
            "Each key maps to a dict with 'type', 'description', and 'required' fields."
        ),
    )
    handler: Callable[[Dict[str, Any], ToolContext], "ToolResult"] = Field(
        ...,
        description="Callable that executes the tool and returns a ToolResult.",
        exclude=True,  # Do not serialise the callable
    )

    class Config:
        arbitrary_types_allowed = True


# ---------------------------------------------------------------------------
# Tool Result and Error
# ---------------------------------------------------------------------------


class ToolError(BaseModel):
    """Typed error returned when a tool invocation fails."""

    tool_name: str = Field(..., description="The tool that failed.")
    error_type: str = Field(
        ...,
        description="Short classifier: 'validation', 'security', 'not_found', 'execution'.",
    )
    message: str = Field(..., description="Safe, client-friendly error description.")


class ToolResult(BaseModel):
    """Typed result from a tool invocation.

    On success, ``data`` carries the tool output (type depends on the tool).
    On failure, ``error`` carries a ``ToolError`` and ``data`` is ``None``.
    A tool must always return a ``ToolResult``; it must never raise an
    unhandled exception.
    """

    tool_name: str = Field(..., description="The tool that produced this result.")
    success: bool = Field(..., description="True if invocation succeeded.")
    data: Optional[Any] = Field(None, description="Typed output on success.")
    error: Optional[ToolError] = Field(None, description="Error detail on failure.")

    @classmethod
    def ok(cls, tool_name: str, data: Any) -> "ToolResult":
        """Convenience constructor for a successful result."""
        return cls(tool_name=tool_name, success=True, data=data, error=None)

    @classmethod
    def fail(
        cls,
        tool_name: str,
        error_type: str,
        message: str,
    ) -> "ToolResult":
        """Convenience constructor for a failed result."""
        return cls(
            tool_name=tool_name,
            success=False,
            data=None,
            error=ToolError(
                tool_name=tool_name,
                error_type=error_type,
                message=message,
            ),
        )
