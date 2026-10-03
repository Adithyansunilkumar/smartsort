"""Repository for persisting Phase 9 Agent Controller runs."""

from datetime import datetime, timezone
from typing import List, Optional
from app.database.connection import get_connection
from app.agent.models import AgentRun
from app.agent.state import AgentState


class AgentRepository:
    """Repository for persisting and retrieving Agent Controller runs."""

    @staticmethod
    def create_run(run: AgentRun) -> None:
        """Persist a new AgentRun."""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    run_id, scan_id, objective, state, current_step,
                    progress, plan_id, created_at, updated_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.scan_id,
                    run.objective,
                    run.state.value if isinstance(run.state, AgentState) else str(run.state),
                    run.current_step,
                    run.progress,
                    run.plan_id,
                    run.created_at,
                    run.updated_at,
                    run.error,
                ),
            )

    @staticmethod
    def get_run(run_id: str) -> Optional[AgentRun]:
        """Retrieve an AgentRun by run_id."""
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return AgentRun(**dict(row))

    @staticmethod
    def update_run_fields(
        run_id: str,
        state: Optional[AgentState] = None,
        current_step: Optional[str] = None,
        progress: Optional[float] = None,
        scan_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[AgentRun]:
        """Update selected fields of an existing AgentRun, bumping updated_at."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        
        updates = ["updated_at = ?"]
        params = [now_iso]

        if state is not None:
            updates.append("state = ?")
            params.append(state.value if isinstance(state, AgentState) else str(state))
        if current_step is not None:
            updates.append("current_step = ?")
            params.append(current_step)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if scan_id is not None:
            updates.append("scan_id = ?")
            params.append(scan_id)
        if plan_id is not None:
            updates.append("plan_id = ?")
            params.append(plan_id)
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        params.append(run_id)

        query = f"UPDATE agent_runs SET {', '.join(updates)} WHERE run_id = ?"

        with get_connection() as conn:
            conn.execute(query, params)

        return AgentRepository.get_run(run_id)

    @staticmethod
    def list_runs(limit: int = 50) -> List[AgentRun]:
        """List most recent runs."""
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return [AgentRun(**dict(row)) for row in cursor.fetchall()]
