"""Static consumer checks; this file never opens a game session.

Run from the repo root with mypy (no runtime dependency):
  mypy --follow-imports=silent --warn-unused-ignores tests/typing/v3_research_contracts.py
For source checks set MYPYPATH=src. The installed-CI check clears MYPYPATH
and PYTHONPATH and supplies --python-executable for the installed SDK.
The ignored errors below must remain errors with --warn-unused-ignores.
"""
from decimal import Decimal
from typing import Any, Literal, assert_type

from pdx_runtime.v3_game import V3Game
from pdx_runtime.v3_game.contracts import (
    OperationResult, PendingResult, ResearchAlreadyCurrent, ResearchChange,
    ResearchCurrent, ResearchCurrentTechnology, ResearchDetails, ResearchNoCurrent,
    ResearchOption, ResearchQueueEntry, ResearchSubmittedChange, ResearchNativeResult,
)
from pdx_runtime.v3_game.research import ResearchAPI
from pdx_runtime.v3_commands import CommandResult


async def game_consumer(game: V3Game) -> Decimal:
    assert_type(game.research, ResearchAPI)
    result = await game.research.details("line_infantry")
    assert_type(result, OperationResult[ResearchDetails])
    while isinstance(result, PendingResult):
        result = await result.resume()
    assert_type(result, ResearchDetails)
    assert_type(result["progress"], Decimal)
    # This slice does not type the other dynamically resolved domains.
    assert_type(game.economy, Any)
    return result["progress"]


async def current(api: ResearchAPI) -> str | None:
    result = await api.current()
    assert_type(result, OperationResult[ResearchCurrent])
    while isinstance(result, PendingResult):
        assert_type(result["status"], Literal["pending"])
        assert_type(result["operation"], str)
        assert_type(result.done, bool)
        assert_type(result.pending_queries, tuple[object, ...])
        result = await result.resume()
    assert_type(result, ResearchCurrent)
    if result["has_current_research"] is True:
        assert_type(result, ResearchCurrentTechnology)
        assert_type(result["progress"], Decimal | None)
        assert_type(result["key"], str)
    else:
        assert_type(result, ResearchNoCurrent)
        assert_type(result["progress"], None)
        assert_type(result["reason"], Literal["no current research"])
    return result["key"]


async def research_methods(api: ResearchAPI) -> None:
    assert_type(await api.options("production"), OperationResult[list[ResearchOption]])
    assert_type(await api.details("line_infantry"), OperationResult[ResearchDetails])
    assert_type(await api.queue(), OperationResult[list[ResearchQueueEntry]])
    result = await api.start("line_infantry")
    assert_type(result, OperationResult[ResearchChange])
    while isinstance(result, PendingResult):
        result = await result
    if result["status"] == "native_result":
        assert_type(result, ResearchNativeResult)
        assert_type(result["native_result"], CommandResult)
        assert_type(result["native_error"], int)
        assert_type(result["native_outcome"], str | None)
        assert_type(result["accepted"], bool | None)
        assert_type(result["submitted"], Literal[True] | None)
        assert_type(result["submission_attempted"], Literal[True])
        assert_type(result["queue"], None)
        assert_type(result["is_current"], None)
        assert_type(result["queue_changed"], None)
        assert_type(result["target_selection_changed"], None)
        assert_type(result["before"], dict[str, object] | None)
        assert_type(result["submit_before"], dict[str, object] | None)
        assert_type(result["pending"], dict[str, object] | None)
        assert_type(result["cleanup"], dict[str, object])
        assert_type(result["reason"], str)
    elif result["submitted"] is False:
        assert_type(result, ResearchAlreadyCurrent)
        assert_type(result["accepted"], None)
    else:
        assert_type(result, ResearchSubmittedChange)
        assert_type(result["accepted"], Literal[True])
        assert_type(result["queue"], list[ResearchQueueEntry])
    assert_type(await api.enqueue("line_infantry"),
                OperationResult[ResearchSubmittedChange | ResearchNativeResult])
    assert_type(await api.dequeue("line_infantry"),
                OperationResult[ResearchSubmittedChange | ResearchNativeResult])


async def pending(handle: PendingResult[ResearchDetails]) -> None:
    assert_type(await handle, OperationResult[ResearchDetails])
    assert_type(await handle.resume(timeout=.5), OperationResult[ResearchDetails])
    assert_type(await handle.resume(), OperationResult[ResearchDetails])
    final: ResearchDetails = await handle  # type: ignore[assignment]
    del final


def reject_invalid_claims(details: ResearchDetails, change: ResearchSubmittedChange,
                          already: ResearchAlreadyCurrent, handle: PendingResult[ResearchDetails]) -> None:
    details["progress"] = 50.0  # type: ignore[typeddict-item]
    details["can_start"] = True  # type: ignore[typeddict-item]
    change["status"] = "succeeded"  # type: ignore[typeddict-item]
    change["accepted"] = False  # type: ignore[typeddict-item]
    already["accepted"] = True  # type: ignore[typeddict-item]
    already["queue"]  # type: ignore[typeddict-item]
    handle["key"]  # type: ignore[call-overload]
    final: ResearchDetails = handle  # type: ignore[assignment]
    del final


def reject_invalid_native_claims(native: ResearchNativeResult) -> None:
    native["status"] = "queued"  # type: ignore[typeddict-item]
    native["accepted"] = 0  # type: ignore[typeddict-item]
    native["submitted"] = False  # type: ignore[typeddict-item]
    native["native_result"] = object()  # type: ignore[typeddict-item]
    native["native_error"] = "574"  # type: ignore[typeddict-item]
    native["native_outcome"] = False  # type: ignore[typeddict-item]
    native["queue"] = []  # type: ignore[typeddict-item]
    native["is_current"] = False  # type: ignore[typeddict-item]
    native["queue_changed"] = False  # type: ignore[typeddict-item]
    accepted: Literal[True] = native["accepted"]  # type: ignore[assignment]
    known_success: ResearchSubmittedChange = native  # type: ignore[assignment]
    del accepted, known_success
