"""Wire-layout and request-copy tests; no game PID, no native-call smoke."""
import ctypes
import struct
import unittest

from pdx_runtime.ck3_native_query import CK3NativeQuery, CharacterResult, QueryStatus


class Thread:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def wait(self): return 0


class Process:
    def __init__(self):
        self.writes = []
        self.freed = []
        self.calls = []
        self.status = QueryStatus()
        self.status.size = ctypes.sizeof(QueryStatus)
        self.status.request_id = 7
        self.status.character.health_raw64 = 0xFEDCBA9876543210
    def resolve_export(self, module, name):
        return 0x5000 if name == "PdxCK3State" else {"PdxCK3QueueCharacter": 0x6000,
            "PdxCK3QueueInterfaceCallback": 0x7000}.get(name, 0x8000)
    def allocate(self, size): return 0x4000
    def write(self, address, data): self.writes.append((address, data))
    def free(self, address): self.freed.append(address)
    def start_thread(self, address, argument):
        self.calls.append((address, argument))
        return Thread()
    def read(self, address, size):
        return ctypes.string_at(ctypes.byref(self.status), size)


class CK3NativeQueryTests(unittest.TestCase):
    def test_native_struct_offsets(self):
        self.assertEqual(ctypes.sizeof(CharacterResult), 48)
        self.assertEqual(ctypes.sizeof(QueryStatus), 168)
        self.assertEqual(QueryStatus.character.offset, 120)
        self.assertEqual(CharacterResult.health_raw64.offset, 32)

    def test_character_request_is_copied_before_storage_is_freed(self):
        process = Process()
        query = CK3NativeQuery(process, 0x1000)
        self.assertEqual(query.queue_character(0xAB000002), 7)
        self.assertEqual(process.writes, [(0x4000, struct.pack("<II", 8, 0xAB000002))])
        self.assertEqual(process.calls, [(0x6000, 0x4000)])
        self.assertEqual(process.freed, [0x4000])

    def test_raw_callback_preserves_opaque_pointer(self):
        process = Process()
        query = CK3NativeQuery(process, 0x1000)
        query.queue_callback(0x100001234, 0x200005678)
        self.assertEqual(process.writes[0][1], struct.pack("<IIQQ", 24, 0, 0x100001234, 0x200005678))
        self.assertEqual(process.calls, [(0x7000, 0x4000)])

    def test_status_keeps_full_raw64_and_reports_sample_copy(self):
        value = CK3NativeQuery(Process(), 0x1000).status()
        self.assertEqual(value["character"]["health_raw64"], 0xFEDCBA9876543210)
        self.assertEqual(value["character"]["health_raw64_hex"], "0xfedcba9876543210")
        self.assertFalse(value["consistent_status_copy"])


if __name__ == "__main__": unittest.main()
