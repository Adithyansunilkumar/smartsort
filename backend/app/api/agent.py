"""API router for Phase 9 Agent Controller."""

from fastapi import APIRouter, Depends, HTTPException, status
from app.agent.controller import AgentController, ActiveRunConflictError
from app.agent.models import AgentRunCreateRequest, AgentRunResponse
from app.agent.state import InvalidStateTransitionError
from app.security.paths import PathSecurityError
from app.services.folder_access import FolderAccessService, get_folder_access_service

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/runs", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
def start_agent_run(
    request: AgentRunCreateRequest,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> AgentRunResponse:
    """Start an agent run workflow: SCAN → ANALYZE → PLAN → WAITING_FOR_APPROVAL."""
    # Ensure authorized root is configured
    try:
        folder_service.require_authorized_root()
    except PathSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ROOT_NOT_CONFIGURED", "message": str(exc)},
        )

    controller = AgentController(folder_service)
    try:
        run = controller.start_run(
            objective=request.objective,
            scan_id=request.scan_id,
        )
        return AgentRunResponse(run=run)
    except ActiveRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RUN_CONFLICT", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(exc)},
        )


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(
    run_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> AgentRunResponse:
    """Retrieve the latest persisted state of an agent run."""
    controller = AgentController(folder_service)
    run = controller.get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": f"Run '{run_id}' not found."},
        )
    return AgentRunResponse(run=run)


@router.post("/runs/{run_id}/cancel", response_model=AgentRunResponse)
def cancel_agent_run(
    run_id: str,
    folder_service: FolderAccessService = Depends(get_folder_access_service),
) -> AgentRunResponse:
    """Cancel an active or waiting agent run. Idempotent."""
    controller = AgentController(folder_service)
    try:
        run = controller.cancel_run(run_id)
        return AgentRunResponse(run=run)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RUN_NOT_FOUND", "message": str(exc)},
        )
    except InvalidStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STATE_TRANSITION", "message": str(exc)},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(exc)},
        )
