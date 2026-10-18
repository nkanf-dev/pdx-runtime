#include "pdx_runtime_dispatch.h"
#include "platform_memory_read.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static HMODULE g_module = nullptr;
static volatile LONG g_initialized = 0;
static const DWORD kMessageBytes = PDX_RUNTIME_MESSAGE_BYTES;
static PdxRuntimeCommandHandler g_handler = nullptr;

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
#if defined(_DLL)
        // Dynamic-CRT development builds can suppress these notifications.
        // The /MT release CRT needs thread attach/detach notifications.
        DisableThreadLibraryCalls(module);
#endif
    }
    return TRUE;
}

DWORD WINAPI PdxRuntimeStatus(PdxRuntimeStatusInfo* result) {
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = {PDX_RUNTIME_PROTOCOL, GetCurrentProcessId(),
        reinterpret_cast<uint64_t>(GetModuleHandleW(nullptr)),
        reinterpret_cast<uint64_t>(g_module), ERROR_SUCCESS, PDX_RUNTIME_MAX_RAW};
    return ERROR_SUCCESS;
}

DWORD WINAPI PdxRuntimeRead(uint64_t address, void* output, uint32_t size, uint32_t* received) {
    return pdx::platform_memory::checked_read(address, output, size, received, PDX_RUNTIME_MAX_RAW,
        [](uintptr_t source, void* destination, uint32_t bytes) {
            SIZE_T count = 0;
            const BOOL ok = ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(source), destination, bytes, &count);
            const auto error = ok ? ERROR_SUCCESS : GetLastError();
            return pdx::platform_memory::ReadResult{ok != FALSE, count, error};
        });
}

DWORD WINAPI PdxRuntimeWrite(uint64_t address, const void* data, uint32_t size, uint32_t* written) {
    if (!data || !written || !size || size > PDX_RUNTIME_MAX_RAW) return ERROR_INVALID_PARAMETER;
    SIZE_T count = 0;
    BOOL ok = WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), data, size, &count);
    *written = static_cast<uint32_t>(count);
    return ok && count == size ? ERROR_SUCCESS : (ok ? ERROR_PARTIAL_COPY : GetLastError());
}

static bool Number(const char* text, uint64_t* result) {
    if (!text || !*text || *text == '-') return false;
    char* end = nullptr;
    *result = _strtoui64(text, &end, 0);
    return end && !*end;
}

static int Nibble(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

static void Error(char* response, DWORD error, uint32_t count = 0) {
    sprintf_s(response, kMessageBytes, "{\"status\":\"error\",\"error\":%lu,\"bytes\":%u}", error, count);
}

void PdxRuntimeDispatchRequest(char* request, char* response,
    PdxRuntimeCommandHandler handler) {
    char* context = nullptr;
    char* command = strtok_s(request, " \r\n\t", &context);
    if (!command) { Error(response, ERROR_INVALID_PARAMETER); return; }
    if (!strcmp(command, "status")) {
        PdxRuntimeStatusInfo status = {};
        PdxRuntimeStatus(&status);
        sprintf_s(response, kMessageBytes,
            "{\"status\":\"ok\",\"protocol\":%u,\"pid\":%u,\"main_module_base\":\"0x%llx\","
            "\"dll_base\":\"0x%llx\",\"error\":%u,\"max_raw\":%u,\"native_context\":\"worker_raw_memory_and_enqueue\"}",
            status.protocol, status.pid, status.main_module_base, status.dll_base, status.error, status.max_raw);
        return;
    }
    if (handler && handler(command, context, response, kMessageBytes)) return;
    uint64_t address = 0;
    if (!Number(strtok_s(nullptr, " \r\n\t", &context), &address)) {
        Error(response, ERROR_INVALID_PARAMETER); return;
    }
    unsigned char bytes[PDX_RUNTIME_MAX_RAW] = {};
    uint32_t count = 0;
    if (!strcmp(command, "read")) {
        uint64_t size = 0;
        if (!Number(strtok_s(nullptr, " \r\n\t", &context), &size) ||
            !size || size > PDX_RUNTIME_MAX_RAW || strtok_s(nullptr, " \r\n\t", &context)) {
            Error(response, ERROR_INVALID_PARAMETER); return;
        }
        DWORD error = PdxRuntimeRead(address, bytes, static_cast<uint32_t>(size), &count);
        if (error) { Error(response, error, count); return; }
        char hex[PDX_RUNTIME_MAX_RAW * 2 + 1] = {};
        const char* digits = "0123456789abcdef";
        for (uint32_t i = 0; i < count; ++i) {
            hex[i * 2] = digits[bytes[i] >> 4];
            hex[i * 2 + 1] = digits[bytes[i] & 15];
        }
        sprintf_s(response, kMessageBytes, "{\"status\":\"ok\",\"error\":0,\"bytes\":%u,\"hex\":\"%s\"}", count, hex);
        return;
    }
    if (!strcmp(command, "write")) {
        char* hex = strtok_s(nullptr, " \r\n\t", &context);
        size_t length = hex ? strlen(hex) : 0;
        if (!length || length % 2 || length > PDX_RUNTIME_MAX_RAW * 2 || strtok_s(nullptr, " \r\n\t", &context)) {
            Error(response, ERROR_INVALID_PARAMETER); return;
        }
        for (size_t i = 0; i < length / 2; ++i) {
            int high = Nibble(hex[i * 2]), low = Nibble(hex[i * 2 + 1]);
            if (high < 0 || low < 0) { Error(response, ERROR_INVALID_PARAMETER); return; }
            bytes[i] = static_cast<unsigned char>((high << 4) | low);
        }
        DWORD error = PdxRuntimeWrite(address, bytes, static_cast<uint32_t>(length / 2), &count);
        if (error) { Error(response, error, count); return; }
        sprintf_s(response, kMessageBytes, "{\"status\":\"ok\",\"error\":0,\"bytes\":%u}", count);
        return;
    }
    Error(response, ERROR_INVALID_FUNCTION);
}

// Each pipe instance owns only its connection. Idle reads and response writes
// never own execution. The adapter still receives one synchronous dispatch at
// a time; its queued native operations outlive any client/pipe instance.
static SRWLOCK g_dispatch_lock = SRWLOCK_INIT;
static HANDLE g_pipe_slots = nullptr;

static HANDLE CreatePipeInstance(bool first = false, DWORD wait_ms = INFINITE) {
    // Windows permits at most PIPE_UNLIMITED_INSTANCES (255) instances. Wait
    // for an actual closed instance at that limit, not a timeout/retry loop.
    const DWORD wait = WaitForSingleObject(g_pipe_slots, wait_ms);
    if (wait != WAIT_OBJECT_0) {
        if (wait == WAIT_TIMEOUT) SetLastError(ERROR_PIPE_BUSY);
        return INVALID_HANDLE_VALUE;
    }
    wchar_t name[96] = {};
    swprintf_s(name, L"\\\\.\\pipe\\pdx-runtime-%lu", GetCurrentProcessId());
    HANDLE pipe = CreateNamedPipeW(name,
        PIPE_ACCESS_DUPLEX | (first ? FILE_FLAG_FIRST_PIPE_INSTANCE : 0),
        PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        PIPE_UNLIMITED_INSTANCES, kMessageBytes, kMessageBytes, 0, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        const DWORD error = GetLastError();
        ReleaseSemaphore(g_pipe_slots, 1, nullptr);
        SetLastError(error);
    }
    return pipe;
}

static void ClosePipeInstance(HANDLE pipe) {
    CloseHandle(pipe);
    ReleaseSemaphore(g_pipe_slots, 1, nullptr);
}

static DWORD WINAPI ServeConnection(void* parameter) {
    HANDLE pipe = static_cast<HANDLE>(parameter);
    DWORD error = ERROR_SUCCESS;
    for (;;) {
        char request[kMessageBytes] = {}, response[kMessageBytes] = {};
        DWORD read = 0;
        if (!ReadFile(pipe, request, kMessageBytes - 1, &read, nullptr)) {
            error = GetLastError();
            break;
        }
        if (!read) break;
        request[read] = 0;
        AcquireSRWLockExclusive(&g_dispatch_lock);
        PdxRuntimeDispatchRequest(request, response, g_handler);
        ReleaseSRWLockExclusive(&g_dispatch_lock);
        DWORD written = 0;
        const DWORD bytes = static_cast<DWORD>(strlen(response));
        if (!WriteFile(pipe, response, bytes, &written, nullptr)) {
            error = GetLastError();
            break;
        }
        if (written != bytes) {
            error = ERROR_PARTIAL_COPY;
            break;
        }
    }
    // EOF affects this connection only. In particular, never reset any native
    // query/command owner or replay a command whose response was not received.
    DisconnectNamedPipe(pipe);
    ClosePipeInstance(pipe);
    return error;
}

static DWORD WINAPI AcceptConnections(void* parameter) {
    struct ListenerLifetime {
        ~ListenerLifetime() {
            // A terminal OS/listener failure must not leave an absent endpoint
            // permanently marked running. Explicit initialize can listen again
            // with the same handler and existing native/connection owners.
            InterlockedExchange(&g_initialized, 0);
        }
    } lifetime;
    HANDLE pipe = static_cast<HANDLE>(parameter);
    for (;;) {
        const BOOL connected = ConnectNamedPipe(pipe, nullptr);
        const DWORD error = connected ? ERROR_SUCCESS : GetLastError();
        const bool accepted = connected || error == ERROR_PIPE_CONNECTED;
        // A short host can connect and exit before the listener is scheduled.
        // Retire that instance, not the process-owned listener.
        if (!accepted && error != ERROR_NO_DATA && error != ERROR_PIPE_NOT_CONNECTED &&
            error != ERROR_BROKEN_PIPE) {
            ClosePipeInstance(pipe);
            return error;
        }
        // Publish the next instance before a fast worker can close the current
        // one. Otherwise a short-host exit could briefly erase the endpoint and
        // be misread as permission to bootstrap another resident runtime.
        HANDLE next = CreatePipeInstance(false, 0);
        const DWORD next_error = next == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
        if (accepted) {
            HANDLE worker = CreateThread(nullptr, 0, ServeConnection, pipe, 0, nullptr);
            if (!worker) {
                const DWORD thread_error = GetLastError();
                DisconnectNamedPipe(pipe);
                ClosePipeInstance(pipe);
                if (next != INVALID_HANDLE_VALUE) ClosePipeInstance(next);
                return thread_error;
            }
            CloseHandle(worker);  // The worker owns this accepted pipe now.
        } else {
            ClosePipeInstance(pipe);
        }
        if (next == INVALID_HANDLE_VALUE) {
            if (next_error != ERROR_PIPE_BUSY) return next_error;
            // At the OS instance limit, all accepted connections are already
            // served. A real connection close releases capacity for listening.
            next = CreatePipeInstance();
            if (next == INVALID_HANDLE_VALUE) return GetLastError();
        }
        pipe = next;
    }
}

DWORD PdxRuntimeStartTransport(PdxRuntimeCommandHandler handler) {
    if (InterlockedCompareExchange(&g_initialized, 1, 0) != 0) return ERROR_ALREADY_INITIALIZED;
    const bool first_start = g_pipe_slots == nullptr;
    if (first_start) {
        g_pipe_slots = CreateSemaphoreW(nullptr, PIPE_UNLIMITED_INSTANCES,
            PIPE_UNLIMITED_INSTANCES, nullptr);
        if (!g_pipe_slots) {
            const DWORD error = GetLastError();
            InterlockedExchange(&g_initialized, 0);
            return error;
        }
    }
    // FIRST_PIPE_INSTANCE protects server identity across competing runtime
    // images; only this first listener uses it. It does not exclude clients.
    HANDLE pipe = CreatePipeInstance(first_start);
    if (pipe == INVALID_HANDLE_VALUE) {
        const DWORD error = GetLastError();
        if (first_start) { CloseHandle(g_pipe_slots); g_pipe_slots = nullptr; }
        InterlockedExchange(&g_initialized, 0);
        return error;
    }
    // Fixed before first publication. An explicit restart after a listener
    // failure keeps this handler and all surviving native/connection owners.
    // No adapter runs under the loader lock.
    if (first_start) g_handler = handler;
    HANDLE listener = CreateThread(nullptr, 0, AcceptConnections, pipe, 0, nullptr);
    if (!listener) {
        const DWORD error = GetLastError();
        ClosePipeInstance(pipe);
        if (first_start) { CloseHandle(g_pipe_slots); g_pipe_slots = nullptr; }
        InterlockedExchange(&g_initialized, 0);
        return error;
    }
    CloseHandle(listener);
    return ERROR_SUCCESS;
}
