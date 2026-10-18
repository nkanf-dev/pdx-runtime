// Isolated source-linked test host. All V3 entrypoints below are stubs.
// No DLL loading, transport initialization, discovery, game or game ABI calls.
#include "../pdx_runtime_dispatch.h"
#include "../v3_bridge_dispatch.h"
#include "../v3_world_entry.h"
#include "../v3_construction.h"
#include <stdio.h>
#include <string.h>

static PdxV3WorldStatusInfo world = {};
static PdxV3ConstructionStatusInfo construction = {};
static unsigned operations = 0, world_reads = 0, construction_reads = 0;
static const char* last_operation = "none";
static uint64_t argument1 = 0, argument2 = 0;
static PdxV3ConstructionRequest submitted = {};
static DWORD operation_error = 0;

static DWORD Record(const char* name, uint64_t first = 0, uint64_t second = 0) {
    ++operations; last_operation = name; argument1 = first; argument2 = second;
    return operation_error;
}
DWORD WINAPI PdxV3Status(PdxV3WorldStatusInfo* result) {
    ++world_reads; *result = world; return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3ConstructionStatus(PdxV3ConstructionStatusInfo* result) {
    ++construction_reads; *result = construction; return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3InstallInterfaceHook(void*) { return Record("hook-install"); }
DWORD WINAPI PdxV3RemoveInterfaceHook(void*) { return Record("hook-remove"); }
DWORD WINAPI PdxV3QueueCreateOptions(void*) { return Record("options-create"); }
DWORD WINAPI PdxV3QueueWorldEntry(uint64_t options) { return Record("world-enter", options); }
DWORD WINAPI PdxV3QueueSubmitOptions(uint64_t options) { return Record("options-submit", options); }
DWORD WINAPI PdxV3QueueDestroyOptions(uint64_t options) { return Record("options-destroy", options); }
DWORD WINAPI PdxV3QueueInterfaceCallback(uint64_t callback, uint64_t opaque) {
    return Record("interface-callback", callback, opaque);
}
DWORD WINAPI PdxV3QueueConstruction(const PdxV3ConstructionRequest* request) {
    submitted = *request;
    return Record("construction-submit", request->actor_id, request->state_id);
}

static bool ForeignDispatch(const char* command, char*, char* response, uint32_t capacity) {
    if (strcmp(command, "foreign-query")) return false;
    strcpy_s(response, capacity, "{\"status\":\"ok\",\"value\":0,\"enabled\":false}");
    return true;
}

static void Emit(const char* name, const char* text,
    PdxRuntimeCommandHandler handler = PdxV3DispatchCommand, DWORD error = 0) {
    operations = world_reads = construction_reads = 0;
    last_operation = "none"; argument1 = argument2 = 0; submitted = {};
    operation_error = error;
    char request[PDX_RUNTIME_MESSAGE_BYTES] = {}, response[PDX_RUNTIME_MESSAGE_BYTES] = {};
    strcpy_s(request, text);
    PdxRuntimeDispatchRequest(request, response, handler);
    printf("{\"case\":\"%s\",\"response\":%s,\"operation\":\"%s\","
        "\"operation_calls\":%u,\"world_reads\":%u,\"construction_reads\":%u,"
        "\"argument1\":%llu,\"argument2\":%llu,"
        "\"submitted_size\":%u,\"submitted_reserved\":%u,\"submitted_type\":%llu}\n",
        name, response, last_operation, operations, world_reads, construction_reads,
        argument1, argument2, submitted.size, submitted.reserved, submitted.building_type);
}

int main() {
    world.size = sizeof(world); world.error = 123; world.installed = 0;
    world.phase = 8; world.pending_operation = 0; world.interface_thread_id = 44;
    world.application_state = 9; world.application_transition_pending = 0;
    world.request_id = 0x100000007ull; world.completed_request_id = 0x100000006ull;
    world.main_module_base = 0x140000000ull; world.application = 0x200000001ull;
    world.original_vtable = 0x200000002ull; world.hook_vtable = 0x200000003ull;
    world.interface_function = 0x200000004ull; world.options_factory = 0x200000005ull;
    world.entry_function = 0x200000006ull; world.options_result = 0;
    world.world = 0x200000007ull; world.active_idler = 0x200000008ull;
    world.active_idler_vtable = 0x200000009ull; world.pending_idler = 0;
    world.frame = 0x100000008ull;
    construction.size = sizeof(construction); construction.error = 456;
    construction.phase = 6; construction.accepted = 0; construction.execution_thread_id = 45;
    construction.effect_observed = 0; construction.request_id = 0x100000009ull;
    construction.completed_request_id = 0x100000008ull;
    construction.main_module_base = 0x140000000ull;
    construction.session = 0x200000010ull; construction.world = 0x200000007ull;
    construction.request = {sizeof(PdxV3ConstructionRequest), 0xffffffffu, 0x80000001u, 0, 0x100000009ull};
    construction.before = {sizeof(PdxV3ConstructionQueueInfo), 0, 0x300000001ull,
        0x300000002ull, 0x300000003ull, 0, 8, 0, 0xffffffffu, 0x80000001u, 0, 0x100000009ull};
    construction.after = construction.before; construction.after.error = 789;
    construction.after.count = 1; construction.after.matching_count = 1;
    construction.after.first_matching_index = 0;

    Emit("status", "status");
    Emit("status_trailing_preserved", "status ignored");
    Emit("empty", " \t\r\n");
    Emit("unknown_empty", "unknown");
    Emit("unknown_number", "unknown 0");
    Emit("unknown_text", "unknown nope");
    Emit("unknown_negative", "unknown -1");
    Emit("unknown_extra", "unknown 123 extra");
    Emit("no_adapter", "world-status", nullptr);
    Emit("foreign_adapter", "foreign-query", ForeignDispatch);
    Emit("foreign_unclaimed", "unknown 0", ForeignDispatch);

    unsigned char sample[] = {0x50, 0x44, 0x58, 0x00, 0x78, 0x36, 0x34, 0xff};
    char command[128] = {};
    sprintf_s(command, "read 0x%llx 8", reinterpret_cast<uint64_t>(sample));
    Emit("raw_read", command);
    Emit("foreign_raw_read", command, ForeignDispatch);
    sprintf_s(command, "write 0x%llx 00FFa5", reinterpret_cast<uint64_t>(sample));
    Emit("raw_write", command);
    sprintf_s(command, "read 0x%llx 3", reinterpret_cast<uint64_t>(sample));
    Emit("raw_read_after_write", command);
    Emit("raw_read_zero", "read 0 0");
    Emit("raw_read_oversize", "read 0 4097");
    Emit("raw_read_extra", "read 0 1 extra");
    Emit("raw_write_odd", "write 0 f");
    Emit("raw_write_invalid", "write 0 zz");
    Emit("raw_write_extra", "write 0 ff extra");

    Emit("world_status", "world-status");
    Emit("hook_install", "hook-install");
    Emit("hook_remove", "hook-remove");
    Emit("options_create", "options-create");
    Emit("world_enter_zero", "world-enter");
    Emit("world_enter_full", "world-enter 0x100000001");
    Emit("options_submit_zero", "options-submit 0");
    Emit("options_destroy_full", "options-destroy 0x100000002");
    Emit("world_error", "hook-remove", PdxV3DispatchCommand, ERROR_BUSY);
    Emit("world_status_argument", "world-status 1");
    Emit("hook_install_argument", "hook-install 1");
    Emit("options_submit_missing", "options-submit");
    Emit("options_destroy_negative", "options-destroy -1");
    Emit("world_enter_extra", "world-enter 1 2");
    Emit("world_unknown", "world-new-command");
    Emit("hook_unknown", "hook-new-command 0");
    Emit("options_unknown", "options-new-command");

    Emit("interface_callback_full", "interface-callback 0x100000003 0x200000004");
    Emit("interface_callback_zero", "interface-callback 0 0");
    Emit("interface_callback_missing", "interface-callback 1");
    Emit("interface_callback_extra", "interface-callback 1 2 3");
    Emit("interface_callback_negative", "interface-callback -1 2");
    Emit("interface_callback_error", "interface-callback 1 2", PdxV3DispatchCommand, ERROR_BUSY);

    Emit("construction_status", "construction-status");
    Emit("construction_submit_full", "construction-submit 4294967295 2147483649 0x100000009");
    Emit("construction_submit_zero", "construction-submit 0 0 0");
    Emit("construction_status_extra", "construction-status 1");
    Emit("construction_submit_missing", "construction-submit 1 2");
    Emit("construction_submit_overflow", "construction-submit 4294967296 2 3");
    Emit("construction_state_overflow", "construction-submit 1 4294967296 3");
    Emit("construction_submit_negative", "construction-submit -1 2 3");
    Emit("construction_submit_extra", "construction-submit 1 2 3 4");
    Emit("construction_unknown", "construction-new-command 0");
    Emit("construction_error", "construction-submit 1 2 3", PdxV3DispatchCommand, ERROR_BUSY);
    return 0;
}
