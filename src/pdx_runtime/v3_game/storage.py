"""Native save catalog and exact save-file operations on the supplied session.

These calls use the game's save manager. They do not change the loaded world.
Deleting a save is an explicit action; catalog/describe never delete anything.
Native results, partial metadata and native message bytes remain available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from .. import v3_lifecycle as life
from ..query_executor import OperationResult

if TYPE_CHECKING:
    from ._core import GameContext

__all__ = ["StorageAPI", "SaveTarget", "SaveCatalog", "SaveDeletion", "SaveRename"]

_MORE_DATA = 234
_UINT32_MAX = (1 << 32) - 1


def _text(value: str, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(name + " must be text")
    if (not empty and not value) or "\0" in value:
        raise ValueError(name + " must be nonempty and cannot contain NUL" if not empty
                         else name + " cannot contain NUL")
    value.encode("utf-8")  # Validate wire text before any batch mutation begins.
    return value


def _capacity(value: int, name: str = "output_capacity") -> int:
    if type(value) is not int or not 0 <= value <= _UINT32_MAX:
        raise ValueError(name + " must be a uint32 byte count")
    return value


@dataclass(frozen=True)
class SaveTarget:
    """An exact native save name/provider or an explicit local native VFS path.

    A catalog descriptor selects its own provider. Local descriptors use their
    exact path; cloud descriptors use their original manager name. No path is
    expanded, normalized or converted to an operating-system filename.
    """

    source: str
    cloud: bool = False
    by_path: bool = False

    def __post_init__(self) -> None:
        _text(self.source, "save source")
        if type(self.cloud) is not bool or type(self.by_path) is not bool:
            raise TypeError("cloud and by_path must be booleans")
        if self.cloud and self.by_path:
            raise ValueError("cloud deletion uses a native save name, not a local VFS path")

    @classmethod
    def from_descriptor(cls, save: life.SaveDescriptor) -> SaveTarget:
        if not isinstance(save, life.SaveDescriptor):
            raise TypeError("save must be a native SaveDescriptor")
        if save.cloud not in (0, 1):
            raise ValueError("native save descriptor has an unknown provider")
        return cls(save.name, cloud=True) if save.cloud else cls(save.path, by_path=True)

    @classmethod
    def native_path(cls, path: str) -> SaveTarget:
        return cls(path, by_path=True)


@dataclass(frozen=True)
class SaveCatalog:
    """Copied native catalog observations, including an earlier short response.

    ``result`` is the most recent observation. Separate attempts are not merged
    into a fictional atomic catalog. All records and raw metadata are preserved.
    """

    attempts: tuple[life.SaveCatalogResult, ...]

    @property
    def result(self) -> life.SaveCatalogResult:
        return self.attempts[-1]

    @property
    def records(self) -> tuple[life.SaveDescriptor, ...]:
        return self.result.records

    @property
    def complete(self) -> bool:
        result = self.result
        return (result.error == 0 and result.count == result.total_count
                and result.count == len(result.records)
                and result.output_size == result.output_required)


@dataclass(frozen=True)
class SaveDeletion:
    """Exact target and the original native deletion result.

    Inspect ``result.native_success`` separately from ``result.error``. A
    successful named local deletion can mean the name was already absent.
    Message truncation never causes this mutation to be submitted again.
    """

    target: SaveTarget
    result: life.SaveManagementResult


@dataclass(frozen=True)
class SaveRename:
    """Original native VFS rename result and the exact requested file/leaf.

    The rename does not overwrite another file or edit saved header metadata.
    native_success, native result tag, callback error and raw message bytes stay
    separate. A short message never causes the rename to be submitted again.
    """

    source_path: str
    new_name: str
    result: life.SaveManagementResult


class StorageAPI:
    """Copied save metadata and explicit native deletion, without a new session."""

    def __init__(self, ctx: GameContext) -> None:
        self._ctx = ctx

    def _options(self) -> dict:
        return {"adapter": self._ctx.adapters.get("lifecycle", "pdx_v3_lifecycle.dll"),
                "process": self._ctx.process}

    async def _catalog(self, label, factory, capacity: int) -> SaveCatalog:
        attempts: list[life.SaveCatalogResult] = []
        while True:
            try:
                result = await self._ctx.query(label, lambda: factory(capacity))
            except BaseException as error:
                # The original query/unknown delivery remains with the shared
                # executor. Add only already copied results to the same error.
                setattr(error, "save_catalog_attempts", tuple(attempts))
                raise
            attempts.append(result)
            if result.error != _MORE_DATA or result.output_required <= capacity:
                return SaveCatalog(tuple(attempts))
            # Only a completed read with an explicit required byte count can
            # request a larger new observation. Never replay a file mutation.
            capacity = result.output_required

    async def catalog(self, *, cloud: bool = False, directory: str = "",
                      name_filter: str = "", output_capacity: int = 65536
                      ) -> OperationResult[SaveCatalog]:
        """List local saves, optionally including cloud saves and invalid headers.

        Native directory/filter syntax is passed unchanged. The result retains
        country_flag_id as a flag resource ID, not an invented country identity.
        A short copied buffer grows to the native required size; known native
        failures return their original records/error instead of an empty list.
        """
        if type(cloud) is not bool:
            raise TypeError("cloud must be a boolean")
        _text(directory, "directory", empty=True)
        _text(name_filter, "name_filter", empty=True)
        capacity = _capacity(output_capacity)
        sources = life.SaveSource.LOCAL | (life.SaveSource.CLOUD if cloud else life.SaveSource(0))

        async def read():
            return await self._catalog("storage.catalog", lambda size: life.list_saves(
                self._ctx.native, directory=directory, name_filter=name_filter,
                sources=sources, output_capacity=size, **self._options()), capacity)
        return await self._ctx.perform("storage.catalog", read)

    async def describe(self, path: str, *, cloud: bool = False,
                       output_capacity: int = 65536) -> OperationResult[SaveCatalog]:
        """Read one exact native save path. This does not load its campaign."""
        _text(path, "path")
        if type(cloud) is not bool:
            raise TypeError("cloud must be a boolean")
        capacity = _capacity(output_capacity)

        async def read():
            return await self._catalog("storage.describe", lambda size: life.describe_save(
                self._ctx.native, path, cloud=cloud, output_capacity=size,
                **self._options()), capacity)
        return await self._ctx.perform("storage.describe", read)

    async def rename(self, path: str, new_name: str, *, message_capacity: int = 4096
                     ) -> OperationResult[SaveRename]:
        """Rename one native VFS save file to a leaf in its current directory.

        Pass an exact VFS path, such as a local catalog descriptor's path, and
        include the desired extension in new_name. Neither input is normalized
        or expanded. This uses the original native rename, without overwrite,
        delete/copy emulation, saved-header edits or a loaded-world change.

        Inspect result.native_success separately from result.error and the
        original result tag. Copied message truncation preserves the original
        rename result without replay. Unknown completion keeps its original
        query; it is not a request to retry the mutation.
        """
        _text(path, "path")
        _text(new_name, "new_name")
        if new_name in (".", "..") or any(char in new_name for char in "/\\:"):
            raise ValueError("new_name must be an explicit filename leaf")
        capacity = _capacity(message_capacity, "message_capacity")

        async def rename_file() -> SaveRename:
            try:
                result = await self._ctx.query("storage.rename", lambda: life.rename_save(
                    self._ctx.native, path, new_name, message_capacity=capacity,
                    **self._options()))
            except BaseException as error:
                setattr(error, "save_rename_source", path)
                setattr(error, "save_rename_new_name", new_name)
                raise
            return SaveRename(path, new_name, result)
        return await self._ctx.perform("storage.rename", rename_file)

    @staticmethod
    def _target(save: str | life.SaveDescriptor | SaveTarget, cloud: bool) -> SaveTarget:
        if isinstance(save, SaveTarget):
            return save
        if isinstance(save, life.SaveDescriptor):
            return SaveTarget.from_descriptor(save)
        if isinstance(save, str):
            return SaveTarget(save, cloud=cloud)
        raise TypeError("save must be a native descriptor, save name or SaveTarget")

    async def _delete(self, target: SaveTarget, message_capacity: int) -> SaveDeletion:
        def submit():
            if target.by_path:
                return life.delete_save(self._ctx.native, target.source,
                    message_capacity=message_capacity, **self._options())
            return life.delete_named_save(self._ctx.native, target.source, cloud=target.cloud,
                message_capacity=message_capacity, **self._options())
        result = await self._ctx.query("storage.delete", submit)
        return SaveDeletion(target, result)

    async def delete(self, save: str | life.SaveDescriptor | SaveTarget, *,
                     cloud: bool = False, message_capacity: int = 4096
                     ) -> OperationResult[SaveDeletion]:
        """Delete exactly one selected save through its original native API.

        A string is a native save name. For an exact local VFS path, use
        ``SaveTarget.native_path(path)`` or pass a catalog descriptor. Descriptors
        and SaveTargets choose their own provider; cloud= applies to strings.
        The target is passed unchanged; the SDK does not expand or search it.
        """
        if type(cloud) is not bool:
            raise TypeError("cloud must be a boolean")
        target = self._target(save, cloud)
        size = _capacity(message_capacity, "message_capacity")
        return await self._ctx.perform("storage.delete", lambda: self._delete(target, size))

    async def delete_many(self, saves: Iterable[str | life.SaveDescriptor | SaveTarget], *,
                          cloud: bool = False, message_capacity: int = 4096
                          ) -> OperationResult[tuple[SaveDeletion, ...]]:
        """Delete each explicitly listed target once, in the supplied order.

        All inputs are validated before the first deletion. Native refusals are
        returned per target. If transport/query completion raises, that original
        error retains ``completed_save_deletions`` and ``pending_save_target``;
        later targets have not been submitted. Resume the original pending work,
        rather than submitting the batch again.
        """
        if type(cloud) is not bool:
            raise TypeError("cloud must be a boolean")
        if isinstance(saves, (str, bytes)):
            raise TypeError("saves must be a sequence of targets, not a single string")
        targets = tuple(self._target(save, cloud) for save in saves)
        size = _capacity(message_capacity, "message_capacity")

        async def remove():
            results: list[SaveDeletion] = []
            for target in targets:
                try:
                    result = await self._delete(target, size)
                except BaseException as error:
                    setattr(error, "completed_save_deletions", tuple(results))
                    setattr(error, "pending_save_target", target)
                    raise
                results.append(result)
            return tuple(results)
        return await self._ctx.perform("storage.delete_many", remove)
