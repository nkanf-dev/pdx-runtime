"""Bounded PE32+ parsing without loading or executing the image."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
from typing import Iterator

AMD64 = 0x8664


def number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected an integer or hex string")
    result = int(value, 0) if isinstance(value, str) else value
    if result < 0:
        raise ValueError("Expected a nonnegative integer")
    return result


def fingerprint(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a full lowercase SHA-256")
    return value


class InvalidImage(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    name: str
    rva: int
    virtual_size: int
    offset: int
    raw_size: int
    flags: int

    @property
    def executable(self) -> bool:
        return bool(self.flags & 0x20000000)


class PEImage:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.data = path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        if self.data[:2] != b"MZ":
            raise InvalidImage("Missing DOS header")
        pe = self.unpack("<I", 0x3C)[0]
        if self.data[pe:pe+4] != b"PE\0\0":
            raise InvalidImage("Missing PE signature")
        self.machine, count = self.unpack("<HH", pe+4)
        optional_size = self.unpack("<H", pe+20)[0]
        optional = pe+24
        if self.unpack("<H", optional)[0] != 0x20B:
            raise InvalidImage("Only PE32+ images are supported")
        self.image_base = self.unpack("<Q", optional+24)[0]
        self.sections: list[Section] = []
        if count > 96:
            raise InvalidImage("Excessive section count")
        for i in range(count):
            cursor = optional+optional_size+i*40
            name, virtual, rva, size, offset, _, _, _, _, flags = self.unpack("<8sIIIIIIHHI", cursor)
            if offset+size > len(self.data):
                raise InvalidImage("Section exceeds file")
            self.sections.append(Section(name.rstrip(b"\0").decode("ascii", "replace"), rva, virtual, offset, size, flags))

    def unpack(self, fmt: str, offset: int) -> tuple:
        size = struct.calcsize(fmt)
        if offset < 0 or offset+size > len(self.data):
            raise InvalidImage(f"Read outside image: {offset:#x}+{size}")
        return struct.unpack_from(fmt, self.data, offset)

    def offset(self, rva: int, size: int = 1) -> int:
        for section in self.sections:
            delta = rva-section.rva
            if 0 <= delta and delta+size <= section.raw_size:
                return section.offset+delta
        raise InvalidImage(f"Unmapped RVA: {rva:#x}+{size}")

    def words(self, rva: int, count: int) -> tuple[int, ...]:
        if count < 0 or count > 65536:
            raise InvalidImage("Unbounded array")
        return self.unpack("<" + "I"*count, self.offset(rva, count*4))

    def executable_va(self, va: int) -> bool:
        return any(s.executable and self.image_base+s.rva <= va < self.image_base+s.rva+s.virtual_size for s in self.sections)

    def strings(self) -> Iterator[tuple[int, str]]:
        for section in self.sections:
            if section.executable:
                continue
            chunk = self.data[section.offset:section.offset+section.raw_size]
            for match in re.finditer(rb"[\x20-\x7e]{4,}\x00", chunk):
                yield section.rva+match.start(), match.group()[:-1].decode("ascii")
