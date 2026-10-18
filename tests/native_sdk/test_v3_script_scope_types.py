"""Live registry wire/ownership contract, without treating fixtures as game data."""
import struct
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_script as script
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

class Memory:
    pid = 123

    def __init__(self):self.base=0x20000;self.memory=bytearray();self.freed=[]
    def resolve_export(self,adapter,symbol, *, aliases=None):
        if aliases is not None and aliases is not V3_EXPORT_ALIASES:
            raise AssertionError("unexpected export-alias policy in V3 CPU fixture")
        self.lookup=(adapter,symbol)
        self.lookup_aliases=aliases
        return 0x10000
    def allocate(self,size):self.memory=bytearray(size);return self.base
    def write(self,address,data):
        offset=address-self.base;self.memory[offset:offset+len(data)]=data
    def read(self,address,size):
        offset=address-self.base;return bytes(self.memory[offset:offset+size])
    def free(self,address):self.freed.append(address)
    def close(self):pass

def row(type,name,save=b'',flags=0,required=None):
    return script.SCOPE_TYPE_ROW.pack(type,1000+type,-1 if not save else 2000+type,
        flags,len(name),len(name) if required is None else required,len(save),len(save),
        0x80000+type*80,1,0,name,save)

class RegistryTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.process=Memory();self.client=Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value={'status':'ok', "error": 0,'request_id':7}
        self.world_status.return_value={'status':'ok','completed_request_id':6}
    def done(self,query,start=4,count=2,total=93,error=0,matched=0,resolved=0,rows=()):
        self.world_status.return_value={'status':'ok','completed_request_id':7}
        f=list(script.SCOPE_TYPES_RESULT.unpack_from(self.process.memory))
        f[1]=error;f[3]=start;f[5]=count;f[6]=start+count;f[7]=total
        f[9]=resolved;f[10]=matched;f[15]=7372
        self.process.write(self.process.base,script.SCOPE_TYPES_RESULT.pack(*f))
        self.process.write(self.process.base+f[16],b''.join(rows))
    def test_paginated_registry_copies_native_names_flags_and_runtime_ids(self):
        query=script.enumerate_scope_types(self.client,start=4,capacity=2,process=self.process)
        f=script.SCOPE_TYPES_RESULT.unpack_from(self.process.memory)
        self.assertEqual(script.SCOPE_TYPE_ROW.size,560);self.assertEqual(script.SCOPE_TYPES_RESULT.size,80)
        self.assertEqual(f[13],self.process.base+80)
        self.assertIsNone(query.read());self.assertFalse(self.process.freed)
        self.done(query,rows=(row(4,b'flag',flags=1),row(5,b'country',b'country',7)))
        result=query.read()
        self.assertEqual((result.start,result.count,result.next,result.total),(4,2,6,93))
        country=result.rows[1]
        self.assertTrue(country.evaluates_triggers and country.executes_effects and country.changes_scopes)
        self.assertEqual(country.reference(0xfe000001),script.Scope.raw(5,0xfe000001))
        self.assertEqual(self.process.freed,[self.process.base])
        self.assertIs(query.read(),result)
    def test_resolve_exact_name_lives_in_same_allocation_until_completion(self):
        query=script.resolve_scope_type(self.client,'custom_registered_scope',process=self.process)
        f=script.SCOPE_TYPES_RESULT.unpack_from(self.process.memory)
        self.assertEqual(f[2],2);self.assertEqual(f[12],self.process.base+80+560)
        self.assertEqual(self.process.memory[640:],b'custom_registered_scope\0')
        self.done(query,start=0,count=1,matched=1,resolved=123,rows=(row(123,b'custom_registered_scope'),))
        result=query.read();self.assertEqual(result.type,123)
        self.assertEqual(script.Scope.id(result.type,9),script.Scope.raw(123,9))
    def test_truncated_native_name_reports_required_count_not_prefix_resolution(self):
        query=script.enumerate_scope_types(self.client,capacity=1,process=self.process)
        self.done(query,start=0,count=1,rows=(row(88,b'x'*255,required=300),))
        result=query.read()
        self.assertFalse(result.rows[0].names_complete)
        self.assertEqual(result.rows[0].name_required,300)
        self.assertEqual(result.next,1)
    def test_not_found_is_not_scope_zero_and_no_retry(self):
        query=script.resolve_scope_type(self.client,'missing_scope',process=self.process)
        self.done(query,start=0,count=0,error=1168)
        result=query.read();self.assertIsNone(result.type);self.assertEqual(result.rows,())
        self.interface_callback.assert_called_once()
    def test_bad_native_count_or_wire_bounds_are_rejected(self):
        data=script.SCOPE_TYPES_RESULT.pack(80,0,1,0,1,2,2,99,0,0,0,0,0,0,560,0,80,560)+bytes(560)
        with self.assertRaises(ValueError):script.ScopeTypesResult.from_bytes(data)
    def test_zero_capacity_is_explicit_query_and_invalid_name_prequeue(self):
        query=script.enumerate_scope_types(self.client,capacity=0,process=self.process)
        self.assertEqual(len(self.process.memory),80)
        with self.assertRaises(ValueError):script.resolve_scope_type(self.client,'bad\0scope',process=self.process)

if __name__=='__main__':unittest.main()
