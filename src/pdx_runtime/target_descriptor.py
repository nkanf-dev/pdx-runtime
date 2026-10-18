"""Generic exact-build file and loaded-image verification for named AMD64 targets.

Callers supply definitions explicitly. Disk SHA-256 selects a definition; the
actual load base only rebases RVAs. Runtime header and code anchor checks are
sampled loaded-image validation, not a full memory SHA-256 or an ABI/semantic
certificate. No game catalog, process discovery, or native call is selected here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
from types import MappingProxyType
from collections.abc import Callable, Iterable, Mapping

from .pe import AMD64, PEImage, fingerprint, number

Read = Callable[[int, int], bytes]


class UnsupportedBuild(ValueError):
    """No exact descriptor was selected; no target address may be guessed."""


class TargetMismatch(ValueError):
    """Selected file or loaded image does not match the descriptor."""


@dataclass(frozen=True)
class FunctionLocation:
    rva: int
    entry_bytes: bytes

    def __post_init__(self):
        number(self.rva)
        if not isinstance(self.entry_bytes, bytes) or not 1 <= len(self.entry_bytes) <= 64:
            raise ValueError('Function entry anchor must contain 1..64 immutable bytes')


@dataclass(frozen=True)
class TargetDefinition:
    game: str
    version: str
    sha256: str
    preferred_image_base: int
    pe_offset: int
    timestamp: int
    image_size: int
    section_count: int
    functions: Mapping[str, FunctionLocation]
    globals: Mapping[str, int]
    layout: Mapping[str, int]
    machine: int = AMD64

    def __post_init__(self):
        fingerprint(self.sha256)
        if (not isinstance(self.game, str) or not self.game.strip()
                or not isinstance(self.version, str) or not self.version.strip() or self.machine != AMD64):
            raise ValueError('A named AMD64 target build is required')
        for value in (self.preferred_image_base, self.pe_offset, self.timestamp,
                      self.image_size, self.section_count):
            number(value)
        if not self.image_size or not 0 < self.section_count <= 96:
            raise ValueError('Invalid image dimensions')
        for field in ('functions', 'globals', 'layout'):
            values = dict(getattr(self, field))
            if (field != 'globals' and not values) or any(not isinstance(key, str) or not key for key in values):
                raise ValueError('Named functions/layout mappings are required')
            if field == 'functions':
                if any(not isinstance(value, FunctionLocation) or
                       value.rva + len(value.entry_bytes) > self.image_size for value in values.values()):
                    raise ValueError('Function anchor exceeds the image')
            else:
                for value in values.values():
                    number(value)
                if field == 'globals' and any(value + 8 > self.image_size for value in values.values()):
                    raise ValueError('Global slot exceeds the image')
            object.__setattr__(self, field, MappingProxyType(values))


_VERIFIED = object()


@dataclass(frozen=True)
class VerifiedTarget:
    """Returned after an exact file check; rechecks loaded anchors before use."""
    definition: TargetDefinition
    _verification: object = field(default=None, repr=False)

    def __post_init__(self):
        if self._verification is not _VERIFIED:
            raise UnsupportedBuild('Use verify_target with the actual image file first')

    def validate_runtime(self, read: Read, image_base: int) -> None:
        d = self.definition
        if type(image_base) is not int or not 0 < image_base <= 0xFFFFFFFFFFFFFFFF - d.image_size:
            raise TargetMismatch('A valid current module load base is required, not a version selector')
        def copied(address, size):
            value = read(address, size)
            if len(value) != size:
                raise TargetMismatch(f'Short runtime identity read: {len(value)} of {size}')
            return value
        dos = copied(image_base, 64)
        if dos[:2] != b'MZ' or struct.unpack_from('<I', dos, 0x3C)[0] != d.pe_offset:
            raise TargetMismatch('Loaded DOS/PE position mismatch')
        header = copied(image_base + d.pe_offset, 84)
        expected = (d.machine, d.section_count, d.timestamp, d.preferred_image_base, d.image_size)
        actual = (struct.unpack_from('<H', header, 4)[0], struct.unpack_from('<H', header, 6)[0],
                  struct.unpack_from('<I', header, 8)[0], struct.unpack_from('<Q', header, 48)[0],
                  struct.unpack_from('<I', header, 80)[0])
        if header[:4] != b'PE\0\0' or struct.unpack_from('<H', header, 24)[0] != 0x20B or actual != expected:
            raise TargetMismatch('Loaded PE identity mismatch')
        for name, location in d.functions.items():
            if copied(image_base + location.rva, len(location.entry_bytes)) != location.entry_bytes:
                raise TargetMismatch('Loaded function anchor mismatch: ' + name)

    def function_address(self, name: str, *, read: Read, image_base: int) -> int:
        """Return a validated rebased location; does not invoke the function."""
        location = self.definition.functions[name]
        self.validate_runtime(read, image_base)
        return image_base + location.rva


def verify_target(binary: Path, *, definitions: Iterable[TargetDefinition]) -> VerifiedTarget:
    """Select from explicit definitions by exact file SHA, never by ASLR/version."""
    image = PEImage(Path(binary))
    choices = tuple(definitions)
    matches = [definition for definition in choices if definition.sha256 == image.sha256]
    if len(matches) != 1:
        raise UnsupportedBuild('No unique exact target descriptor for SHA-256 ' + image.sha256)
    d = matches[0]
    pe = image.unpack('<I', 0x3C)[0]
    actual = (image.machine, image.image_base, pe, image.unpack('<I', pe + 8)[0],
              image.unpack('<I', pe + 80)[0], len(image.sections))
    expected = (d.machine, d.preferred_image_base, d.pe_offset, d.timestamp, d.image_size, d.section_count)
    if actual != expected:
        raise TargetMismatch('File PE metadata does not match its exact descriptor')
    for name, location in d.functions.items():
        offset = image.offset(location.rva, len(location.entry_bytes))
        sections = [section for section in image.sections if section.rva <= location.rva and
                    location.rva + len(location.entry_bytes) <= section.rva + min(section.raw_size, section.virtual_size)]
        if (len(sections) != 1 or not sections[0].executable or
                not sections[0].flags & 0x40000000 or sections[0].flags & 0x80000000 or
                image.data[offset:offset + len(location.entry_bytes)] != location.entry_bytes):
            raise TargetMismatch('File function anchor mismatch: ' + name)
    for name, rva in d.globals.items():
        sections = [section for section in image.sections if section.rva <= rva and
                    rva + 8 <= section.rva + section.virtual_size]
        if len(sections) != 1 or sections[0].executable or not sections[0].flags & 0x40000000:
            raise TargetMismatch('Global slot is not in readable data: ' + name)
    return VerifiedTarget(d, _VERIFIED)



def verify_loaded_target(process, image_base: int, *, definitions: Iterable[TargetDefinition]) -> VerifiedTarget:
    """Bind exact file identity to the already supplied process/module.

    A single process-owned cache avoids rehashing the executable for every small
    query. File/module identity changes invalidate it. Runtime anchors are always
    checked again. This cache is not a pointer lease or a full memory hash.
    """
    choices = tuple(definitions)
    try:
        modules = process.modules()
        matches = [module for module in modules if number(module.get('base')) == image_base]
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise TargetMismatch('Current module identity could not be read: ' + str(error)) from error
    if len(matches) != 1 or not matches[0].get('path'):
        raise UnsupportedBuild('No unique current module path for the supplied load base')
    path = Path(matches[0]['path']).resolve()
    try:
        stat = path.stat()
    except OSError as error:
        raise TargetMismatch('Current module file is unavailable: ' + str(error)) from error
    file_key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    key = (process.pid, image_base, file_key, tuple(id(choice) for choice in choices))
    cached = getattr(process, '_pdx_verified_target_file', None)
    if cached is not None and cached[0] == key:
        verified = cached[1]
    else:
        verified = verify_target(path, definitions=choices)
        after = path.stat()
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != file_key[1:]:
            raise TargetMismatch('Module file changed during exact fingerprint validation')
        process._pdx_verified_target_file = (key, verified)
    try:
        verified.validate_runtime(process.read, image_base)
    except (OSError, ValueError, TypeError, struct.error) as error:
        if isinstance(error, TargetMismatch):
            raise
        raise TargetMismatch('Loaded target identity could not be read: ' + str(error)) from error
    return verified
