"""Guards the single-source-of-truth marker vocabulary in
`src/code_work_markers.py` against the exact bug class that motivated it:
a phrase recognized by `ExecutionWorkflow`'s inner gate but not by
`RequestRouter`'s outer classification, so the workflow is never reached.
"""

from __future__ import annotations

import pytest

from src.code_work_markers import (
    CODE_INTEGRATION_MARKERS,
    CODE_PLANNING_MARKERS,
    PLAN_FOLLOW_MARKERS,
)
from src.request_router import RequestIntent, RequestRouter


def test_every_plan_follow_marker_is_also_a_code_planning_marker() -> None:
    """`ExecutionWorkflow` treats these as read-only-plan.md phrases; they
    must also be present in the router's unconditional planning markers so
    a fresh thread with no prior context still reaches the workflow."""
    for marker in PLAN_FOLLOW_MARKERS:
        assert marker in CODE_PLANNING_MARKERS


@pytest.mark.parametrize("marker", [*CODE_PLANNING_MARKERS, *CODE_INTEGRATION_MARKERS])
def test_unconditional_code_marker_routes_to_code_work_without_thread_context(
    marker: str,
) -> None:
    """Every unconditional code-work/plan-following marker must, by itself,
    route to CODE_WORK even as the very first message in a brand-new
    thread — no `ThreadWorkContext` involved."""
    routed = RequestRouter().route(f"my-project {marker} 해줘")

    assert routed.intent is RequestIntent.CODE_WORK
