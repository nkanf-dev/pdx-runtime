"""CPU native-executor boundary only. No PID/game or original native target run."""
import asyncio
import struct
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import unittest
from types import SimpleNamespace
from pdx_runtime import native_call, v3_access, target_descriptor as td, v3_target_profiles as profiles
from pdx_runtime.v3_game.diplomacy.treaty import proposal as mature
from pdx_runtime.v3_game.diplomacy import _treaty_catalog as catalog
from pdx_runtime.v3_game.diplomacy import DiplomacyAPI
from tests.core.test_target_descriptor import inert_pe, definition
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

BASE=0x140000000
RPM=0x71000000
BATCH=0x71000100
ACCESS=0x71000200
SLOT=0x21000000
DB=0x22000000
ARRAY=0x23000000
PROVIDER_RVA=profiles.VICTORIA3_1_13_11.functions["article_type_provider"].rva
LAYOUT=profiles.VICTORIA3_1_13_11.layout

class Memory:
    pid=24680
    def __init__(self):
        self.blocks={};self.next=0x50000000;self.freed=[];self.exports={}
    def modules(self):return list(self.module_records)
    def allocate(self,n):
        self.next=(self.next+0x10000+0xfff)&~0xfff;self.blocks[self.next]=bytearray(n);return self.next
    def add(self,a,n):self.blocks[a]=bytearray(n);return self.blocks[a]
    def _find(self,a,n):
        for base,block in self.blocks.items():
            if base<=a and a+n<=base+len(block):return block,a-base
        raise OSError('synthetic unreadable range')
    def read(self,a,n):b,o=self._find(a,n);return bytes(b[o:o+n])
    def write(self,a,value):b,o=self._find(a,len(value));b[o:o+len(value)]=value
    def free(self,a):self.freed.append(a);del self.blocks[a]
    def resolve_export(self,adapter,name, *, aliases=None):
        if aliases is not None and aliases is not V3_EXPORT_ALIASES:
            raise AssertionError("unexpected export-alias policy in V3 CPU fixture")
        address={'ReadProcessMemory':RPM,'PdxNativeCallBatchInterface':BATCH,'PdxV3AccessCallback':ACCESS}[name]
        self.exports[address]=name;return address

class Executor:
    pid=24680
    def __init__(self,process,keys):
        self.process=process;self.world=0x33445566;self.request_id=0;self.completed_request_id=0
        self.queue_count=0;self.targets=[];self.sizes=[];self.fail_source=None;self.nil_db=False
        self.unknown=False;self.change_count_on_second=False
        self.provider_rva=PROVIDER_RVA;self.keys=keys;self.setup(keys)
    def setup(self,keys):
        p=self.process;p.add(SLOT,8)[:]=struct.pack('<Q',DB)
        header=p.add(DB,0x90);struct.pack_into('<Qii',header,0x80,ARRAY,len(keys),len(keys))
        array=p.add(ARRAY,max(8,len(keys)*8))
        for i,key in enumerate(keys):
            address=0x24000000+i*0x10000;entry=p.add(address,0x48)
            struct.pack_into('<I',entry,0x10,i);entry[0x40]=1;entry[0x41]=2
            encoded=key.encode();struct.pack_into('<2Q',entry,0x28,len(encoded),15 if len(encoded)<=15 else len(encoded)+7)
            if len(encoded)<=15:entry[0x18:0x18+len(encoded)]=encoded
            else:
                heap=0x34000000+i*0x10000;p.add(heap,len(encoded))[:]=encoded;struct.pack_into('<Q',entry,0x18,heap)
            struct.pack_into('<Q',array,i*8,address)
    def v3_world_status(self):return {'status':'ok','world':hex(self.world),'main_module_base':hex(BASE),'transition_pending':False,'request_id':self.request_id,'completed_request_id':self.completed_request_id}
    def v3_dispatch_status(self):return {'status':'ok','request_id':self.request_id,'completed_request_id':self.completed_request_id}
    def request(self, command):
        """CPU transport; legacy-named methods below are fixture model hooks only."""
        if command == "world-status":
            return self.v3_world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            if command != f"interface-callback {callback:#x} {address:#x}":
                raise AssertionError(f"noncanonical V3 fixture wire command: {command!r}")
            return self.queue_v3_interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")
    def execute_batch(self,address):
        p=self.process;header=list(native_call.BATCH_REQUEST.unpack(p.read(address,32)))
        for index in range(header[2]):
            record=header[3]+index*56;fields=list(native_call.REQUEST.unpack(p.read(record,56)))
            target,n,args=fields[2:5];argv=struct.unpack('<'+str(n)+'Q',p.read(args,n*8))
            self.targets.append(target)
            if target==BASE+self.provider_rva:
                if self.change_count_on_second and self.queue_count==2:struct.pack_into('<i',p.blocks[DB],0x8C,len(self.keys)+1)
                if self.nil_db:struct.pack_into('<Q',p.blocks[SLOT],0,0)
                rax=SLOT
            else:
                assert target==RPM,'no unsafe keyview or other native consumer after probes'
                handle,source,destination,size,received=argv
                assert handle==0xFFFFFFFFFFFFFFFF and size>0
                self.sizes.append(size)
                try:
                    if self.fail_source is not None and source==self.fail_source:raise OSError('injected partial RPM')
                    data=p.read(source,size);p.write(destination,data);p.write(received,struct.pack('<Q',size));rax=1
                except OSError:p.write(received,bytes(8));rax=0
            fields[0]=56;fields[1]=0;fields[5]=rax;p.write(record,native_call.REQUEST.pack(*fields))
            p.write(address+24,struct.pack('<Q',index+1))
        p.write(address+4,bytes(4))
    def queue_v3_interface_callback(self,callback,address):
        assert callback==ACCESS
        self.queue_count+=1;self.request_id+=1
        if self.unknown:raise OSError('synthetic queue outcome unknown')
        p=self.process;f=list(v3_access.REQUEST.unpack(p.read(address,v3_access.REQUEST.size)))
        assert f[2:4]==[1,0] and f[4]==BATCH and f[5]==address+v3_access.PAYLOAD_OFFSET
        self.execute_batch(f[5])
        f[1]=0;f[9]=0x88000000;f[10]=0x88000000;f[11]=1234;f[12]=1;f[13]=1
        p.write(address,v3_access.REQUEST.pack(*f));self.completed_request_id=self.request_id
        return {'status':'ok', "error": 0,'request_id':self.request_id}

class CatalogTests(unittest.TestCase):
    def direct(self,keys,index=0,capacity=0,configure=None):
        p=Memory();client=Executor(p,keys)
        if configure:configure(client)
        initial,prepare,decode=catalog.compose_article_type(read_process_memory=RPM,provider_address=BASE+client.provider_rva,layout=LAYOUT,ordinal=index,key_capacity=capacity)
        address=p.allocate(len(initial));p.write(address,prepare(address));client.execute_batch(address)
        return decode(p.read(address,len(initial))),client,decode
    def context(self,keys):
        p=Memory();client=Executor(p,keys)
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        raw=inert_pe();binary=Path(temporary.name)/"synthetic.exe";binary.write_bytes(raw)
        client.definition=definition(raw,game="victoria3",layout=LAYOUT);client.provider_rva=client.definition.functions["article_type_provider"].rva
        p.module_records=[{'base':BASE,'path':str(binary)}]
        p.add(BASE,0x200)[:]=raw[:0x200]
        p.add(BASE+0x1000,0x200)[:]=raw[0x200:0x400]
        ctx=SimpleNamespace(process=p,native=client,image_base=BASE,world=client.world,
            diagnostics={},adapters={'call':'call','access':'access'})
        async def country_id():return 1
        ctx.country_id=country_id
        state=mature.accept._state(client,p,1,BASE,{'data':'data','access':'access','call':'call','script':'script','commands':'commands'})
        state['world']=client.world
        return ctx,state,client
    def run_catalog(self,ctx,state,client):
        with patch.object(profiles,'KNOWN_TARGETS',(client.definition,)):
            return asyncio.run(catalog.article_types(ctx,state))
    def test_sso_cap0_no_read_zero_and_all_owned_pointer_sources_scrubbed(self):
        row,client,decode=self.direct(['defensive_pact'])
        self.assertTrue(row['valid']);self.assertTrue(row['key_complete']);self.assertEqual(row['key'],'defensive_pact')
        self.assertFalse(row['key_copy_requested']);self.assertTrue(row['borrowed_storage_scrubbed'])
        self.assertTrue(all(n>0 for n in client.sizes));self.assertEqual(decode.layout['received_width'],8)
        self.assertNotIn(BASE+0x7B37F0,client.targets)
    def test_heap_metadata_then_fresh_full_utf8_copy(self):
        key='long_treaty_key_共同防御'
        metadata,_,_=self.direct([key]);self.assertTrue(metadata['valid']);self.assertFalse(metadata['key_complete'])
        self.assertEqual(metadata['key_length'],len(key.encode()))
        row,_,_=self.direct([key],capacity=len(key.encode()));self.assertTrue(row['key_complete']);self.assertEqual(row['key'],key)
    def test_kernel_rpm_reads_prefix_and_heap_over4096(self):
        key='x'*5000
        row,client,_=self.direct([key],capacity=5000);self.assertTrue(row['key_complete']);self.assertEqual(row['key'],key)
        self.assertIn(5000,client.sizes)
        keys=['k'+str(i) for i in range(601)]
        row,client,_=self.direct(keys,index=600);self.assertTrue(row['key_complete']);self.assertEqual(row['key'],'k600')
        self.assertIn(601*8,client.sizes)
    def test_nil_db_and_failed_prefix_have_no_unsafe_following_callee(self):
        row,client,_=self.direct(['defensive_pact'],configure=lambda c:setattr(c,'nil_db',True))
        self.assertFalse(row['header_copied']);self.assertFalse(row['valid']);self.assertIsNone(row['count'])
        self.assertTrue(all(t in (BASE+0x7B6A80,RPM) for t in client.targets))
        row,_,_=self.direct(['defensive_pact'],configure=lambda c:setattr(c,'fail_source',ARRAY))
        self.assertFalse(row['valid']);self.assertIsNone(row['key']);self.assertTrue(row['borrowed_storage_scrubbed'])
    def test_out_of_range_and_short_heap_copy_not_complete(self):
        row,_,_=self.direct(['defensive_pact'],index=1);self.assertFalse(row['valid'])
        row,_,_=self.direct(['long_key_more_than15'],capacity=2)
        self.assertTrue(row['valid']);self.assertFalse(row['key_complete']);self.assertIsNone(row['key'])
    def test_async_real_access_callbackresult_and_shared_operation(self):
        ctx,state,client=self.context(['defensive_pact','long_key_more_than15'])
        rows=self.run_catalog(ctx,state,client)
        self.assertEqual([r['key'] for r in rows],['defensive_pact','long_key_more_than15'])
        self.assertEqual(client.queue_count,3);self.assertIsNone(state['pending']);self.assertEqual(len(ctx.process.freed),3)
    def test_empty_db_is_true_empty_not_failed_read(self):
        ctx,state,client=self.context([]);self.assertEqual(self.run_catalog(ctx,state,client),[])
        self.assertEqual(client.queue_count,1)
    def test_async_key_change_fails_without_unbounded_retry(self):
        ctx,state,client=self.context(['long_key_more_than15']);client.change_count_on_second=True
        with self.assertRaisesRegex(RuntimeError,'changed'):self.run_catalog(ctx,state,client)
        self.assertEqual(client.queue_count,2)
    def test_unknown_query_retains_original_handle_and_prevents_reconstruction(self):
        ctx,state,client=self.context(['defensive_pact']);client.unknown=True
        with self.assertRaises(OSError) as caught:self.run_catalog(ctx,state,client)
        self.assertIs(state['pending']['query'],caught.exception.query)
        self.assertIn(caught.exception.query.result_address,ctx.process.blocks);self.assertFalse(ctx.process.freed)
        with self.assertRaises(RuntimeError):self.run_catalog(ctx,state,client)
        self.assertEqual(client.queue_count,1)

    def test_diplomacy_actual_catalog_uses_descriptor_layout_and_reuses_file_hash(self):
        ctx,state,client=self.context(['defensive_pact'])
        client.definition=replace(client.definition,layout=dict(client.definition.layout,definition_kind=0x42))
        entry=ctx.process.blocks[0x24000000];entry[0x40]=0;entry[0x42]=1
        with patch.object(profiles,'KNOWN_TARGETS',(client.definition,)),patch.object(td,'PEImage',wraps=td.PEImage) as image:
            first=asyncio.run(DiplomacyAPI(ctx)._article_catalog())
            second=asyncio.run(DiplomacyAPI(ctx)._article_catalog())
        self.assertEqual([row['key'] for row in first],['defensive_pact'])
        self.assertEqual(first[0]['kind'],'mutual')
        self.assertEqual(second[0]['kind'],'mutual')
        self.assertEqual(image.call_count,1)
        self.assertEqual(client.queue_count,2)
        self.assertTrue(all(target in (BASE+0x1000,RPM) for target in client.targets))
    def test_diplomacy_unknown_file_and_runtime_anchor_mismatch_do_not_call_provider(self):
        for failure in ('unknown_file','bad_anchor','runtime_read_error'):
            ctx,state,client=self.context(['defensive_pact'])
            if failure=='bad_anchor':ctx.process.blocks[BASE+0x1000][0]^=1
            elif failure=='runtime_read_error':del ctx.process.blocks[BASE+0x1000]
            else:Path(ctx.process.module_records[0]['path']).write_bytes(inert_pe()+b'unknown-build')
            with patch.object(profiles,'KNOWN_TARGETS',(client.definition,)):
                with self.assertRaises((td.UnsupportedBuild,td.TargetMismatch)) as caught:
                    asyncio.run(DiplomacyAPI(ctx)._article_catalog())
            self.assertTrue(str(caught.exception))
            self.assertEqual(client.queue_count,0)
            self.assertEqual(client.targets,[])
    def test_automatic_catalog_supplies_v3_definitions_before_exports_or_dispatch(self):
        ctx,state,client=self.context(['defensive_pact'])
        with patch.object(catalog,'verify_loaded_target',side_effect=td.UnsupportedBuild('unknown exact build')) as gate:
            with self.assertRaises(td.UnsupportedBuild):
                asyncio.run(catalog.article_types(ctx,state))
        gate.assert_called_once_with(ctx.process,ctx.image_base,definitions=profiles.KNOWN_TARGETS)
        self.assertEqual(ctx.process.exports,{})
        self.assertEqual(client.queue_count,0)
        self.assertEqual(ctx.process.freed,[])

    def test_raw_composer_accepts_explicit_provider_without_known_build_selection(self):
        p=Memory();client=Executor(p,['defensive_pact']);client.provider_rva=0x123456
        initial,prepare,decode=catalog.compose_article_type(read_process_memory=RPM,
            provider_address=BASE+client.provider_rva,layout=LAYOUT,ordinal=0)
        address=p.allocate(len(initial));p.write(address,prepare(address));client.execute_batch(address)
        self.assertEqual(decode(p.read(address,len(initial)))['key'],'defensive_pact')
        self.assertIn(BASE+0x123456,client.targets)

if __name__=='__main__':unittest.main(verbosity=2)
