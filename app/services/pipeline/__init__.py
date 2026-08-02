"""Pipeline package: chains prediction generation, optimization, and
reporting into one call - Phase 10's integration seam."""

from app.services.pipeline.service import PipelineResult, WeeklyPipelineService

__all__ = [
    "PipelineResult",
    "WeeklyPipelineService",
]
