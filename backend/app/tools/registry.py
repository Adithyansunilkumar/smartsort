"""Phase 10 — Tool Registry.

The ToolRegistry is the single, application-level authority for:

* registering tool definitions (rejected if duplicate name)
* listing all registered tools
* looking up a single tool by name
* safely invoking a tool by name with validated inputs and a ToolContext

Architecture
------------
Tools are registered at application startup from ``app.tools.definitions``.
The registry is a lightweight in-process singleton; there is no database or
network dependency in the registry layer itself.

Invocation safety contract
--------------------------
* The registry catches ALL exceptions from a tool handler and wraps them in a
  ``ToolResult.fail`` response.  A tool invocation never propagates an
  unhandled exception to the caller.
* Input validation is performed against the tool's declared ``input_schema``
  before the handler is called.
* Unknown tool names return a typed error, not a KeyError.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.tools.models import (
    MutationPolicy,
    ToolContext,
    ToolDefinition,
    ToolResult,
)

logger = logging.getLogger(__name__)


class DuplicateToolError(Exception):
    """Raised when a tool with the same name is registered twice."""


class ToolRegistry:
    """Centralised registry and invocation layer for SmartSort tools.

    Usage
    -----
    ::

        registry = ToolRegistry()
        registry.register(my_tool_definition)

        result = registry.invoke("tool_name", inputs, context)

    Design notes
    ------------
    * Registration is intentionally *not* thread-safe because it is expected
      to happen once at startup in a single-threaded context.  Invocations
      themselves are stateless and safe to call from multiple threads.
    * The registry never imports service modules directly; it relies on
      handlers embedded in ToolDefinition objects supplied at registration.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: ToolDefinition) -> None:
        """Register a ToolDefinition.

        Parameters
        ----------
        tool:
            A fully constructed ToolDefinition.

        Raises
        ------
        DuplicateToolError
            If a tool with the same ``name`` has already been registered.
        """
        if tool.name in self._tools:
            raise DuplicateToolError(
                f"Tool '{tool.name}' is already registered. "
                "Each tool name must be unique."
            )
        self._tools[tool.name] = tool
        logger.debug("Registered tool '%s' (%s)", tool.name, tool.category.value)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_tools(self) -> List[ToolDefinition]:
        """Return all registered tools ordered by name."""
        return sorted(self._tools.values(), key=lambda t: t.name)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Return a ToolDefinition by name, or None if not found."""
        return self._tools.get(name)

    def tool_names(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def invoke(
        self,
        tool_name: str,
        inputs: Dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Invoke a registered tool by name with validated inputs.

        The method follows this pipeline:

        1. Lookup — reject unknown tool names.
        2. Input validation — verify required parameters are present.
        3. Execution — call the handler inside a broad try/except.
        4. Return — always a ``ToolResult`` (success or failure).

        Parameters
        ----------
        tool_name:
            The registered tool name.
        inputs:
            A free-form dict of input parameters.  Contents are validated
            against ``tool.input_schema`` before the handler is called.
        context:
            Runtime context (folder_service, etc.) passed to the handler.

        Returns
        -------
        ToolResult
            Always returned; never raises.
        """
        # 1. Lookup
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult.fail(
                tool_name=tool_name,
                error_type="not_found",
                message=f"Unknown tool '{tool_name}'. "
                f"Available tools: {', '.join(self.tool_names()) or 'none'}.",
            )

        # 2. Input validation
        validation_error = self._validate_inputs(tool, inputs)
        if validation_error:
            return ToolResult.fail(
                tool_name=tool_name,
                error_type="validation",
                message=validation_error,
            )

        # 3. Execution
        try:
            result = tool.handler(inputs, context)
            # Ensure the handler returned a ToolResult
            if not isinstance(result, ToolResult):
                logger.error(
                    "Tool '%s' handler returned %r instead of ToolResult",
                    tool_name,
                    type(result),
                )
                return ToolResult.fail(
                    tool_name=tool_name,
                    error_type="execution",
                    message=f"Tool '{tool_name}' returned an unexpected result type.",
                )
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool '%s' handler raised an unhandled exception", tool_name)
            safe_message = self._sanitize_exception(exc)
            return ToolResult.fail(
                tool_name=tool_name,
                error_type="execution",
                message=safe_message,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_inputs(tool: ToolDefinition, inputs: Dict[str, Any]) -> Optional[str]:
        """Validate ``inputs`` against ``tool.input_schema``.

        Returns an error string if validation fails, or None if valid.
        Only checks presence of required fields declared in the schema.
        """
        schema = tool.input_schema
        if not schema:
            return None  # Tool accepts no declared inputs

        missing = []
        for field_name, field_meta in schema.items():
            if field_meta.get("required", False) and field_name not in inputs:
                missing.append(field_name)

        if missing:
            return (
                f"Missing required input parameter(s) for tool '{tool.name}': "
                + ", ".join(f"'{m}'" for m in missing)
                + "."
            )

        return None

    @staticmethod
    def _sanitize_exception(exc: Exception) -> str:
        """Return a safe, client-friendly error message."""
        from app.security.paths import PathSecurityError

        if isinstance(exc, PathSecurityError):
            return f"Security boundary violation: {exc}"
        msg = str(exc)
        if len(msg) > 300:
            msg = msg[:297] + "..."
        return msg if msg else "An unexpected error occurred during tool execution."
