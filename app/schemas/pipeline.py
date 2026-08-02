"""API-facing schema for the Phase 10 pipeline endpoint."""

from __future__ import annotations

from pydantic import BaseModel

from app.services.pipeline.service import PipelineResult


class PipelineResponse(BaseModel):
    """Response for `POST /api/v1/pipeline/run/{gameweek}`."""

    gameweek: int
    model_used: str
    players_processed: int
    predictions_created: int
    predictions_updated: int
    predictions_evaluated_previous_gw: int
    report_markdown: str
    duration_seconds: float

    model_config = {"protected_namespaces": ()}

    @classmethod
    def from_result(cls, result: PipelineResult) -> "PipelineResponse":
        return cls(
            gameweek=result.gameweek,
            model_used=result.generation.model_used,
            players_processed=result.generation.players_processed,
            predictions_created=result.generation.predictions_created,
            predictions_updated=result.generation.predictions_updated,
            predictions_evaluated_previous_gw=(
                result.evaluation.predictions_evaluated
                if result.evaluation is not None
                else 0
            ),
            report_markdown=result.report.to_markdown(),
            duration_seconds=result.duration_seconds,
        )
