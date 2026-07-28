"""
Unit tests for ``app.services.fpl_api.client.FPLAPIClient``.

All HTTP calls are mocked with ``respx`` — these tests never touch
the real FPL API, so they run offline, deterministically, and fast,
and won't break if FPL happens to be down or rate-limiting us.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.fpl_settings import FPLAPISettings
from app.services.fpl_api.client import FPLAPIClient
from app.services.fpl_api.endpoints import FPL_BASE_URL
from app.services.fpl_api.exceptions import (
    FPLNotFoundError,
    FPLResponseParsingError,
)

MINIMAL_BOOTSTRAP = {
    "events": [
        {
            "id": 1,
            "name": "Gameweek 1",
            "deadline_time": "2026-08-15T17:30:00Z",
            "finished": True,
            "is_previous": True,
            "is_current": False,
            "is_next": False,
        }
    ],
    "teams": [
        {
            "id": 1,
            "name": "Arsenal",
            "short_name": "ARS",
            "strength": 4,
            "strength_overall_home": 1250,
            "strength_overall_away": 1300,
            "strength_attack_home": 1200,
            "strength_attack_away": 1250,
            "strength_defence_home": 1300,
            "strength_defence_away": 1350,
        }
    ],
    "element_types": [
        {
            "id": 3,
            "singular_name": "Midfielder",
            "singular_name_short": "MID",
            "squad_select": 5,
            "squad_min_play": 2,
            "squad_max_play": 5,
        }
    ],
    "elements": [
        {
            "id": 328,
            "first_name": "Bukayo",
            "second_name": "Saka",
            "web_name": "Saka",
            "team": 1,
            "element_type": 3,
            "now_cost": 100,
            "selected_by_percent": "45.2",
            "form": "6.5",
            "points_per_game": "5.8",
            "status": "a",
        }
    ],
    "game_settings": {
        "squad_squadsize": 15,
        "squad_squadplay": 11,
        "squad_team_limit": 3,
        "squad_total_spend": 1000,
    },
}


@pytest.fixture
def fast_settings() -> FPLAPISettings:
    """Settings with near-zero backoff so retry tests run instantly."""
    return FPLAPISettings(retry_backoff_seconds=0.01, max_retries=2)


@pytest.mark.asyncio
async def test_get_bootstrap_static_parses_successfully(fast_settings: FPLAPISettings) -> None:
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        mock.get("/bootstrap-static/").mock(
            return_value=httpx.Response(200, json=MINIMAL_BOOTSTRAP)
        )
        async with FPLAPIClient(settings=fast_settings) as client:
            result = await client.get_bootstrap_static()

    assert result.teams[0].name == "Arsenal"
    assert result.elements[0].web_name == "Saka"
    assert result.game_settings.squad_team_limit == 3


@pytest.mark.asyncio
async def test_get_element_summary_404_raises_not_found(fast_settings: FPLAPISettings) -> None:
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        mock.get("/element-summary/999999/").mock(return_value=httpx.Response(404))
        async with FPLAPIClient(settings=fast_settings) as client:
            with pytest.raises(FPLNotFoundError):
                await client.get_element_summary(999999)


@pytest.mark.asyncio
async def test_malformed_response_raises_parsing_error(fast_settings: FPLAPISettings) -> None:
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        # Missing every required field -> should fail Pydantic validation.
        mock.get("/bootstrap-static/").mock(return_value=httpx.Response(200, json={}))
        async with FPLAPIClient(settings=fast_settings) as client:
            with pytest.raises(FPLResponseParsingError):
                await client.get_bootstrap_static()


@pytest.mark.asyncio
async def test_transient_500_is_retried_then_succeeds(fast_settings: FPLAPISettings) -> None:
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        route = mock.get("/bootstrap-static/")
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json=MINIMAL_BOOTSTRAP),
        ]
        async with FPLAPIClient(settings=fast_settings) as client:
            result = await client.get_bootstrap_static()

    assert route.call_count == 3
    assert result.teams[0].name == "Arsenal"


@pytest.mark.asyncio
async def test_persistent_500_exhausts_retries_and_raises(fast_settings: FPLAPISettings) -> None:
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        mock.get("/bootstrap-static/").mock(return_value=httpx.Response(500))
        async with FPLAPIClient(settings=fast_settings) as client:
            with pytest.raises(Exception):  # FPLServerError, via tenacity reraise
                await client.get_bootstrap_static()


@pytest.mark.asyncio
async def test_get_fixtures_filters_by_event(fast_settings: FPLAPISettings) -> None:
    fixture_payload = [
        {
            "id": 1,
            "event": 8,
            "kickoff_time": "2026-10-10T14:00:00Z",
            "finished": False,
            "team_h": 1,
            "team_a": 2,
            "team_h_difficulty": 2,
            "team_a_difficulty": 4,
        }
    ]
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        mock.get("/fixtures/", params={"event": 8}).mock(
            return_value=httpx.Response(200, json=fixture_payload)
        )
        async with FPLAPIClient(settings=fast_settings) as client:
            fixtures = await client.get_fixtures(event=8)

    assert len(fixtures) == 1
    assert fixtures[0].team_h_difficulty == 2
    assert fixtures[0].team_a_difficulty == 4


@pytest.mark.asyncio
async def test_bulk_element_summaries_isolates_individual_failures(
    fast_settings: FPLAPISettings,
) -> None:
    summary_payload = {"fixtures": [], "history": [], "history_past": []}
    with respx.mock(base_url=FPL_BASE_URL) as mock:
        mock.get("/element-summary/1/").mock(
            return_value=httpx.Response(200, json=summary_payload)
        )
        mock.get("/element-summary/2/").mock(return_value=httpx.Response(404))

        async with FPLAPIClient(settings=fast_settings) as client:
            results = await client.get_element_summaries_bulk([1, 2])

    assert results[1].fixtures == []
    assert isinstance(results[2], FPLNotFoundError)
