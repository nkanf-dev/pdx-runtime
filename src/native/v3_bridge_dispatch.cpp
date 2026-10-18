#include "v3_bridge_dispatch.h"
#include "v3_world_entry.h"
#include "v3_construction.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool Number(const char* text, uint64_t* result) {
    if (!text || !*text || *text == '-') return false;
    char* end = nullptr;
    *result = _strtoui64(text, &end, 0);
    return end && !*end;
}

static void WorldResponse(char* response, uint32_t response_capacity, DWORD operation_error = 0) {
    PdxV3WorldStatusInfo world = {};
    PdxV3Status(&world);
    sprintf_s(response, response_capacity,
        "{\"status\":\"%s\",\"error\":%lu,\"world_error\":%u,\"installed\":%u,"
        "\"phase\":%u,\"pending_operation\":%u,\"interface_thread_id\":%u,"
        "\"application_state\":%u,\"transition_pending\":%u,"
        "\"request_id\":%llu,\"completed_request_id\":%llu,"
        "\"main_module_base\":\"0x%llx\",\"application\":\"0x%llx\","
        "\"original_vtable\":\"0x%llx\",\"hook_vtable\":\"0x%llx\","
        "\"interface_function\":\"0x%llx\",\"options_factory\":\"0x%llx\","
        "\"entry_function\":\"0x%llx\",\"options_result\":\"0x%llx\","
        "\"world\":\"0x%llx\",\"active_idler\":\"0x%llx\","
        "\"active_idler_vtable\":\"0x%llx\",\"pending_idler\":\"0x%llx\",\"frame\":%llu}",
        operation_error ? "error" : "ok", operation_error, world.error,
        world.installed, world.phase, world.pending_operation, world.interface_thread_id,
        world.application_state, world.application_transition_pending,
        world.request_id, world.completed_request_id,
        world.main_module_base, world.application, world.original_vtable, world.hook_vtable,
        world.interface_function, world.options_factory, world.entry_function, world.options_result,
        world.world, world.active_idler, world.active_idler_vtable, world.pending_idler, world.frame);
}

static bool WorldDispatch(const char* command, char* context, char* response, uint32_t response_capacity) {
    if (strncmp(command, "world-", 6) && strncmp(command, "hook-", 5) && strncmp(command, "options-", 8)) return false;
    char* argument = strtok_s(nullptr, " \r\n\t", &context);
    if (strtok_s(nullptr, " \r\n\t", &context)) {
        WorldResponse(response, response_capacity, ERROR_INVALID_PARAMETER); return true;
    }
    uint64_t options = 0;
    if (argument && !Number(argument, &options)) {
        WorldResponse(response, response_capacity, ERROR_INVALID_PARAMETER); return true;
    }
    DWORD error = ERROR_SUCCESS;
    if (!strcmp(command, "world-status") && !argument) {
        // Snapshot was sampled on the native interface callback, not here.
    } else if (!strcmp(command, "hook-install") && !argument) {
        error = PdxV3InstallInterfaceHook(nullptr);
    } else if (!strcmp(command, "hook-remove") && !argument) {
        error = PdxV3RemoveInterfaceHook(nullptr);
    } else if (!strcmp(command, "options-create") && !argument) {
        error = PdxV3QueueCreateOptions(nullptr);
    } else if (!strcmp(command, "world-enter")) {
        error = PdxV3QueueWorldEntry(options);
    } else if (!strcmp(command, "options-submit") && argument) {
        error = PdxV3QueueSubmitOptions(options);
    } else if (!strcmp(command, "options-destroy") && argument) {
        error = PdxV3QueueDestroyOptions(options);
    } else {
        error = ERROR_INVALID_PARAMETER;
    }
    WorldResponse(response, response_capacity, error);
    return true;
}

static void ConstructionResponse(char* response, uint32_t response_capacity, DWORD operation_error = 0) {
    PdxV3ConstructionStatusInfo value = {};
    PdxV3ConstructionStatus(&value);
    sprintf_s(response, response_capacity,
        "{\"status\":\"%s\",\"error\":%lu,\"construction_error\":%u,\"phase\":%u,"
        "\"accepted\":%u,\"execution_thread_id\":%u,\"effect_observed\":%u,"
        "\"request_id\":%llu,\"completed_request_id\":%llu,\"main_module_base\":\"0x%llx\","
        "\"session\":\"0x%llx\",\"world\":\"0x%llx\",\"actor_id\":%u,"
        "\"state_id\":%u,\"building_type\":\"0x%llx\","
        "\"before\":{\"error\":%u,\"country\":\"0x%llx\",\"queue\":\"0x%llx\","
        "\"records\":\"0x%llx\",\"count\":%u,\"capacity\":%u,\"matching_count\":%u,\"first_matching_index\":%u},"
        "\"after\":{\"error\":%u,\"country\":\"0x%llx\",\"queue\":\"0x%llx\","
        "\"records\":\"0x%llx\",\"count\":%u,\"capacity\":%u,\"matching_count\":%u,\"first_matching_index\":%u}}",
        operation_error ? "error" : "ok", operation_error, value.error, value.phase,
        value.accepted, value.execution_thread_id, value.effect_observed,
        value.request_id, value.completed_request_id, value.main_module_base,
        value.session, value.world, value.request.actor_id, value.request.state_id, value.request.building_type,
        value.before.error, value.before.country, value.before.queue, value.before.records,
        value.before.count, value.before.capacity, value.before.matching_count, value.before.first_matching_index,
        value.after.error, value.after.country, value.after.queue, value.after.records,
        value.after.count, value.after.capacity, value.after.matching_count, value.after.first_matching_index);
}

static bool ConstructionDispatch(const char* command, char* context, char* response, uint32_t response_capacity) {
    if (strncmp(command, "construction-", 13)) return false;
    if (!strcmp(command, "construction-status")) {
        ConstructionResponse(response, response_capacity, strtok_s(nullptr, " \r\n\t", &context) ? ERROR_INVALID_PARAMETER : 0);
        return true;
    }
    uint64_t actor = 0, state = 0, type = 0;
    if (strcmp(command, "construction-submit") ||
        !Number(strtok_s(nullptr, " \r\n\t", &context), &actor) || actor > UINT32_MAX ||
        !Number(strtok_s(nullptr, " \r\n\t", &context), &state) || state > UINT32_MAX ||
        !Number(strtok_s(nullptr, " \r\n\t", &context), &type) ||
        strtok_s(nullptr, " \r\n\t", &context)) {
        ConstructionResponse(response, response_capacity, ERROR_INVALID_PARAMETER); return true;
    }
    PdxV3ConstructionRequest request = {sizeof(PdxV3ConstructionRequest),
        static_cast<uint32_t>(actor), static_cast<uint32_t>(state), 0, type};
    ConstructionResponse(response, response_capacity, PdxV3QueueConstruction(&request));
    return true;
}

bool PdxV3DispatchCommand(const char* command, char* context,
    char* response, uint32_t response_capacity) {
    if (!strcmp(command, "interface-callback")) {
        uint64_t callback = 0, opaque = 0;
        if (!Number(strtok_s(nullptr, " \r\n\t", &context), &callback) ||
            !Number(strtok_s(nullptr, " \r\n\t", &context), &opaque) ||
            strtok_s(nullptr, " \r\n\t", &context)) {
            WorldResponse(response, response_capacity, ERROR_INVALID_PARAMETER); return true;
        }
        WorldResponse(response, response_capacity, PdxV3QueueInterfaceCallback(callback, opaque));
        return true;
    }
    if (WorldDispatch(command, context, response, response_capacity)) return true;
    if (ConstructionDispatch(command, context, response, response_capacity)) return true;
    return false;
}
