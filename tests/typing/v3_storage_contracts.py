"""Static public StorageAPI usage. No SDK connection or game calls run here."""
from typing import assert_type
from pdx_runtime.v3_game import V3Game
from pdx_runtime import v3_lifecycle as life
from pdx_runtime.query_executor import OperationResult
from pdx_runtime.v3_game.storage import SaveCatalog, SaveDeletion, SaveTarget, StorageAPI

async def contracts(game: V3Game, api: StorageAPI, descriptor: life.SaveDescriptor) -> None:
    assert_type(game.storage, StorageAPI)
    listing = await api.catalog(cloud=True)
    assert_type(listing, OperationResult[SaveCatalog])
    described = await api.describe("native/save/path", cloud=False)
    assert_type(described, OperationResult[SaveCatalog])
    deleted = await api.delete(descriptor)
    assert_type(deleted, OperationResult[SaveDeletion])
    batch = await api.delete_many((descriptor, SaveTarget("named", cloud=True)))
    assert_type(batch, OperationResult[tuple[SaveDeletion, ...]])
    if isinstance(listing, SaveCatalog):
        assert_type(listing.records, tuple[life.SaveDescriptor, ...])
        assert_type(listing.result, life.SaveCatalogResult)
        assert_type(listing.complete, bool)
    if isinstance(deleted, SaveDeletion):
        assert_type(deleted.result, life.SaveManagementResult)
        assert_type(deleted.target, SaveTarget)
