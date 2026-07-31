"""Squad management package: syncs 'My Squad' from the FPL API into SquadState/SquadPlayer."""

from app.services.squad.service import SquadSyncResult, SquadSyncService

__all__ = [
    "SquadSyncResult",
    "SquadSyncService",
]
