"""Static public state-management usage; no runtime/game work is performed."""
from typing import assert_type
from pdx_runtime.v3_game import V3Game
from pdx_runtime import v3_data as data
from pdx_runtime.query_executor import OperationResult
from pdx_runtime.v3_game.state_management import CopiedValue, StateIncorporation, StateManagementAPI

async def contracts(game: V3Game, api: StateManagementAPI) -> None:
    assert_type(game.state_management, StateManagementAPI)
    report = await api.incorporation(1)
    assert_type(report, OperationResult[StateIncorporation])
    batch = await api.incorporation_many((1, 2))
    assert_type(batch, OperationResult[tuple[StateIncorporation, ...]])
    if isinstance(report, StateIncorporation):
        assert_type(report.native_page, data.ProjectionPage)
        assert_type(report.cells, dict[str, data.ProjectionCell])
        assert_type(report.values, dict[str, CopiedValue])
        assert_type(report.missing_fields, tuple[str, ...])
    from pdx_runtime.v3_game.state_management import IncorporationAction
    submitted = await api.incorporate(1)
    assert_type(submitted, OperationResult[IncorporationAction])
    validated = await api.incorporate(1, validate_only=True)
    assert_type(validated, OperationResult[IncorporationAction])

    from pdx_runtime.v3_game.state_management import StateNames, StateRenameResult
    names = await api.names(1)
    assert_type(names, OperationResult[StateNames])
    if isinstance(names, StateNames):
        assert_type(names.values, dict[str, str | None])
        assert_type(names.fields, dict[str, data.DataResult])
        assert_type(names.requested_fields, tuple[str, ...])
    renamed = await api.rename(1, state="New state", city="", port="Port", farm="Farm",
                               mine="Mine", wood="Wood")
    assert_type(renamed, OperationResult[StateRenameResult])
    checked = await game.state_management.rename(1, city="City", validate_only=True)
    assert_type(checked, OperationResult[StateRenameResult])
