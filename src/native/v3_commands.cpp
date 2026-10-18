#include "v3_commands.h"
#include <cstddef>
#include <cstring>
#include <limits>

namespace {
constexpr uintptr_t application_rva = 0x592d128;
constexpr uintptr_t session_rva = 0x5956f70;
constexpr uintptr_t registry_rva = 0x59020e0;
constexpr uintptr_t token_registry_rva = 0x592ebd0;
struct Registration { uint32_t token, index; };
// Each pair is the actual slot9 token and startup LEA factory / MOV table[index]
// registration in this build. Abstract RTTI and duplicate helper bases excluded.
constexpr Registration registrations[] = {
    {681u, 681u},
    {682u, 682u},
    {684u, 684u},
    {1405u, 1405u},
    {1426u, 1426u},
    {1458u, 1458u},
    {1466u, 1466u},
    {1468u, 1468u},
    {1471u, 1471u},
    {1500u, 1500u},
    {1560u, 1560u},
    {1561u, 1561u},
    {1562u, 1562u},
    {1563u, 1563u},
    {1564u, 1564u},
    {1688u, 1688u},
    {1695u, 1695u},
    {1696u, 1696u},
    {1697u, 1697u},
    {1698u, 1698u},
    {1699u, 1699u},
    {1700u, 1700u},
    {1701u, 1701u},
    {1702u, 1702u},
    {1703u, 1703u},
    {1704u, 1704u},
    {1750u, 1750u},
    {1751u, 1751u},
    {1752u, 1752u},
    {1753u, 1753u},
    {1758u, 1758u},
    {1961u, 1961u},
    {1962u, 1962u},
    {10367u, 2367u},
    {10368u, 2368u},
    {10543u, 2543u},
    {10618u, 2618u},
    {10619u, 2619u},
    {10680u, 2680u},
    {11340u, 3340u},
    {11878u, 3878u},
    {12426u, 4426u},
    {12490u, 4490u},
    {12771u, 4771u},
    {12797u, 4797u},
    {12800u, 4800u},
    {12833u, 4833u},
    {12854u, 4854u},
    {12858u, 4858u},
    {12859u, 4859u},
    {13057u, 5057u},
    {13084u, 5084u},
    {13156u, 5156u},
    {13208u, 5208u},
    {13277u, 5277u},
    {13279u, 5279u},
    {13281u, 5281u},
    {13303u, 5303u},
    {13310u, 5310u},
    {13311u, 5311u},
    {14177u, 6177u},
    {14248u, 6248u},
    {14273u, 6273u},
    {14275u, 6275u},
    {16140u, 8140u},
    {17211u, 9211u},
    {17227u, 9227u},
    {17228u, 9228u},
    {17234u, 9234u},
    {17268u, 9268u},
    {17292u, 9292u},
    {20240u, 12240u},
    {20250u, 12250u},
    {20288u, 12288u},
    {20289u, 12289u},
    {21256u, 13256u},
    {21275u, 13275u},
    {21289u, 13289u},
    {21292u, 13292u},
    {21308u, 13308u},
    {21382u, 13382u},
    {21528u, 13528u},
    {21542u, 13542u},
    {21543u, 13543u},
    {21544u, 13544u},
    {21567u, 13567u},
    {21581u, 13581u},
    {21612u, 13612u},
    {21614u, 13614u},
    {21677u, 13677u},
    {21681u, 13681u},
    {21700u, 13700u},
    {21702u, 13702u},
    {21767u, 13767u},
    {21803u, 13803u},
    {21836u, 13836u},
    {21885u, 13885u},
    {21892u, 13892u},
    {21905u, 13905u},
    {22041u, 14041u},
    {22112u, 14112u},
    {22204u, 14204u},
    {22275u, 14275u},
    {22276u, 14276u},
    {22303u, 14303u},
    {22400u, 14400u},
    {22403u, 14403u},
    {22405u, 14405u},
    {22408u, 14408u},
    {22410u, 14410u},
    {22414u, 14414u},
    {22418u, 14418u},
    {22419u, 14419u},
    {22473u, 14473u},
    {22510u, 14510u},
    {22519u, 14519u},
    {22544u, 14544u},
    {22672u, 14672u},
    {22673u, 14673u},
    {22684u, 14684u},
    {22698u, 14698u},
    {22702u, 14702u},
    {22791u, 14791u},
    {22797u, 14797u},
    {22799u, 14799u},
    {22844u, 14844u},
    {22970u, 14970u},
    {22980u, 14980u},
    {22988u, 14988u},
    {23018u, 15018u},
    {23020u, 15020u},
    {23033u, 15033u},
    {23034u, 15034u},
    {23075u, 15075u},
    {23112u, 15112u},
    {23179u, 15179u},
    {23181u, 15181u},
    {23182u, 15182u},
    {23185u, 15185u},
    {23186u, 15186u},
    {23192u, 15192u},
    {23201u, 15201u},
    {23330u, 15330u},
    {23331u, 15331u},
    {23333u, 15333u},
    {23339u, 15339u},
    {23340u, 15340u},
    {23343u, 15343u},
    {23399u, 15399u},
    {23404u, 15404u},
    {23410u, 15410u},
    {23476u, 15476u},
    {23495u, 15495u},
    {23496u, 15496u},
    {23497u, 15497u},
    {23566u, 15566u},
    {23601u, 15601u},
    {23613u, 15613u},
    {23646u, 15646u},
    {23668u, 15668u},
    {23669u, 15669u},
    {23670u, 15670u},
    {23678u, 15678u},
    {23679u, 15679u},
    {23684u, 15684u},
    {23685u, 15685u},
    {23689u, 15689u},
    {23728u, 15728u},
    {23734u, 15734u},
    {23739u, 15739u},
    {23740u, 15740u},
    {23755u, 15755u},
    {23761u, 15761u},
    {23767u, 15767u},
    {23824u, 15824u},
    {23838u, 15838u},
    {23873u, 15873u},
    {23883u, 15883u},
    {23895u, 15895u},
    {24072u, 16072u},
    {24157u, 16157u},
    {24251u, 16251u},
    {24301u, 16301u},
    {24393u, 16393u},
    {24394u, 16394u},
    {24476u, 16476u},
    {24477u, 16477u},
    {24653u, 16653u},
    {24654u, 16654u},
    {24657u, 16657u},
    {24660u, 16660u},
    {24677u, 16677u},
    {24694u, 16694u},
    {24714u, 16714u},
    {24780u, 16780u},
    {24813u, 16813u},
    {24856u, 16856u},
    {24857u, 16857u},
    {25015u, 17015u},
    {25016u, 17016u},
    {25054u, 17054u},
    {25055u, 17055u},
    {25207u, 17207u},
    {25210u, 17210u},
    {25226u, 17226u},
    {25231u, 17231u},
    {25233u, 17233u},
    {25237u, 17237u},
    {25270u, 17270u},
    {25271u, 17271u},
    {25275u, 17275u},
    {25277u, 17277u},
    {25459u, 17459u},
    {25481u, 17481u},
    {25491u, 17491u},
    {25533u, 17533u},
    {25537u, 17537u},
    {25550u, 17550u},
    {25560u, 17560u},
    {25562u, 17562u},
    {25586u, 17586u},
    {25587u, 17587u},
    {25636u, 17636u},
    {25637u, 17637u},
    {25638u, 17638u},
    {25639u, 17639u},
    {25640u, 17640u},
    {25641u, 17641u},
    {25687u, 17687u},
    {25707u, 17707u},
    {25732u, 17732u},
    {25733u, 17733u},
    {25734u, 17734u},
    {25735u, 17735u},
    {25736u, 17736u},
    {25737u, 17737u},
    {25738u, 17738u},
    {25771u, 17771u},
    {26113u, 18113u},
};
using Destroy = void* (__fastcall*)(void*, uint32_t);
using Type = uint32_t (__fastcall*)(void*);
using Clone = void** (__fastcall*)(void*, void**);
using Validate = unsigned char (__fastcall*)(void*, void*);
using Receive = unsigned char (__fastcall*)(void*, void**, uint32_t);
using PersistentIo = void (__fastcall*)(void*, void*);
using FileBorrow = void* (__fastcall*)(void*, void*, uint32_t, uint32_t);
using Dtor = void (__fastcall*)(void*);
using TokenName = void* (__fastcall*)(uint32_t);
using GuardEnter = void* (__fastcall*)(void*, uint32_t, int, const char*);
using GuardLeave = void (__fastcall*)(void*);
struct TokenReadLock {
    volatile LONG* value;
    explicit TokenReadLock(void* registry) : value(static_cast<volatile LONG*>(registry)) {
        // Exact CBinLexer/TokenName native shared lock protocol.
        while ((InterlockedExchangeAdd(value, 2) & 1) != 0) {
            InterlockedExchangeAdd(value, -2);
            while ((*value & 1) != 0) YieldProcessor();
        }
    }
    ~TokenReadLock() { InterlockedExchangeAdd(value, -2); }
    TokenReadLock(const TokenReadLock&) = delete;
    TokenReadLock& operator=(const TokenReadLock&) = delete;
};
struct alignas(16) Storage { unsigned char bytes[80] = {}; };
struct Access {
    uintptr_t base;
    Storage storage;
    explicit Access(uintptr_t value) : base(value) {
        reinterpret_cast<GuardEnter>(base + 0x33bd640)(storage.bytes, 1, 0,
            "pdx-runtime/native/v3_commands.cpp");
    }
    ~Access() { reinterpret_cast<GuardLeave>(base + 0x33bd750)(storage.bytes); }
    Access(const Access&) = delete;
    Access& operator=(const Access&) = delete;
};
uintptr_t base() { return reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)); }
template<class T> T& field(void* object, size_t offset) {
    return *reinterpret_cast<T*>(static_cast<unsigned char*>(object) + offset);
}
template<class T> T slot(uint64_t command, size_t index) {
    return reinterpret_cast<T>((*reinterpret_cast<uintptr_t**>(command))[index]);
}
void* live_factory(const Registration& value) {
    return *reinterpret_cast<void**>(base() + registry_rva + value.index * sizeof(void*));
}
struct File {
    alignas(16) unsigned char bytes[96] = {};
    File(void* buffer, uint32_t length) {
        // 3aff620 borrows bytes; +55 is false, so dtor never owns this buffer.
        reinterpret_cast<FileBorrow>(base() + 0x3aff620)(bytes, buffer, length, 0);
    }
    explicit File(void* native_vector) {
        using FromVector = void* (__fastcall*)(void*, void*, uint32_t, uint32_t, bool);
        // Actual338cad0 file constructor: vector borrowed, mode1 writable.
        reinterpret_cast<FromVector>(base() + 0x3aff580)(bytes, native_vector, 0, 1, false);
    }
    ~File() { reinterpret_cast<Dtor>(base() + 0x3aff770)(bytes); }
    File(const File&) = delete;
    File& operator=(const File&) = delete;
};
struct Writer {
    uintptr_t vtable;
    void* file;
    int32_t depth;
    uint8_t binary, owns_file, extra;
    uint8_t padding;
    explicit Writer(void* value) : vtable(base() + 0x48504f0), file(value),
        depth(0), binary(1), owns_file(0), extra(1), padding(0) {}
};
static_assert(sizeof(Writer) == 24 && offsetof(Writer, binary) == 0x14);
static_assert(sizeof(PdxV3CommandResult) == 88);
static_assert(sizeof(PdxV3CommandRegistration) == 144);
void token(Writer* writer, uint32_t value) {
    using WriteToken = void (__fastcall*)(void*, uint32_t);
    reinterpret_cast<WriteToken>(base() + 0x3b547d0)(writer, value);
}
DWORD perform(PdxV3CommandResult* result) {
    switch (result->action) {
    case PDX_V3_COMMAND_ENUMERATE:
        return PdxV3CommandEnumerate(reinterpret_cast<PdxV3CommandRegistration*>(result->buffer),
            result->buffer_capacity / sizeof(PdxV3CommandRegistration), &result->count);
    case PDX_V3_COMMAND_CREATE:
        return PdxV3CommandFactory(result->type_token, &result->command);
    case PDX_V3_COMMAND_DESERIALIZE:
        return PdxV3CommandDeserialize(reinterpret_cast<void*>(result->buffer),
            result->buffer_size, &result->command);
    case PDX_V3_COMMAND_SERIALIZE:
        return PdxV3CommandSerialize(result->command, reinterpret_cast<void*>(result->buffer),
            result->buffer_capacity, &result->buffer_size);
    case PDX_V3_COMMAND_VALIDATE:
        result->buffer_size = 0;
        if (result->buffer_capacity)
            return PdxV3CommandValidateText(result->command, reinterpret_cast<void*>(result->buffer),
                result->buffer_capacity, &result->buffer_size, &result->validated);
        return PdxV3CommandValidate(result->command, &result->validated);
    case PDX_V3_COMMAND_DESTROY:
        return PdxV3CommandDestroy(&result->command);
    case PDX_V3_COMMAND_SUBMIT:
        return PdxV3CommandSubmit(&result->command, result->submit_flags, &result->accepted);
    case PDX_V3_COMMAND_CLONE:
        if (!result->buffer || result->buffer_capacity < sizeof(uint64_t)) return ERROR_INVALID_PARAMETER;
        result->buffer_size = sizeof(uint64_t);
        return PdxV3CommandClone(result->command, reinterpret_cast<uint64_t*>(result->buffer));
    case PDX_V3_COMMAND_TOKEN_NAME:
        return PdxV3CommandTokenNameText(result->type_token, reinterpret_cast<void*>(result->buffer),
            result->buffer_capacity, &result->buffer_size);
    case PDX_V3_COMMAND_RESOLVE_TOKEN:
        if (result->buffer_size > result->buffer_capacity) return ERROR_INVALID_PARAMETER;
        return PdxV3CommandResolveToken(reinterpret_cast<const char*>(result->buffer),
            result->buffer_size, &result->type_token);
    default:
        return ERROR_INVALID_PARAMETER;
    }
}
void callback(void* application, void* session, void* opaque) {
    auto* result = static_cast<PdxV3CommandResult*>(opaque);
    if (!result) return;
    result->error = ERROR_INVALID_PARAMETER;
    if (result->size != sizeof(*result)) return;
    result->execution_thread_id = GetCurrentThreadId();
    result->validated = result->accepted = result->count = 0;
    const uintptr_t image = base();
    auto* app = *reinterpret_cast<void**>(image + application_rva);
    if (!app || (application && application != app) || field<uint8_t>(app, 0x258)) {
        result->error = ERROR_NOT_READY; return;
    }
    const uint64_t world = field<uint64_t>(app, 0x608);
    // Expected world is explicit for operations on retained native pointers.
    if (result->world && result->world != world) { result->error = ERROR_INVALID_STATE; return; }
    result->application = reinterpret_cast<uint64_t>(app);
    result->world = world;
    result->session = image + session_rva;
    if (session && reinterpret_cast<uintptr_t>(session) != image + session_rva) return;
    if (session && result->action != PDX_V3_COMMAND_SUBMIT) return;
    if (result->action == PDX_V3_COMMAND_SUBMIT && !session) {
        result->error = ERROR_INVALID_THREAD_ID; return;
    }
    if (result->action == PDX_V3_COMMAND_VALIDATE && !world) {
        result->error = ERROR_NOT_READY; return;
    }
    try {
        if (session) {
            // Session receiver owns its actual command-domain protocol. The
            // Interface guard is not valid evidence for nesting on this thread.
            result->error = perform(result);
        } else {
            Access scope(image);
            result->error = perform(result);
        }
        if (!result->error && result->command)
            result->error = PdxV3CommandType(result->command, &result->type_token);
    } catch (...) { result->error = ERROR_UNHANDLED_EXCEPTION; }
}
}
DWORD WINAPI PdxV3CommandFactory(uint32_t type_token, uint64_t* owned) {
    if (!owned || *owned) return ERROR_INVALID_PARAMETER;
    // Raw engine factory has no proven table bound. The caller is responsible
    // for a valid registered token, just as when calling3371000 directly.
    using EngineFactory = void* (__fastcall*)(uint32_t);
    try {
        *owned = reinterpret_cast<uint64_t>(reinterpret_cast<EngineFactory>(base() + 0x3371000)(type_token));
        return *owned ? ERROR_SUCCESS : ERROR_NOT_ENOUGH_MEMORY;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandClone(uint64_t source, uint64_t* owned) {
    if (!source || !owned || *owned) return ERROR_INVALID_PARAMETER;
    try {
        slot<Clone>(source, 8)(reinterpret_cast<void*>(source), reinterpret_cast<void**>(owned));
        return *owned ? ERROR_SUCCESS : ERROR_NOT_ENOUGH_MEMORY;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandDestroy(uint64_t* owned) {
    if (!owned) return ERROR_INVALID_PARAMETER;
    if (!*owned) return ERROR_SUCCESS;
    const uint64_t command = *owned;
    *owned = 0;
    try { slot<Destroy>(command, 0)(reinterpret_cast<void*>(command), 1); return 0; }
    catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandType(uint64_t command, uint32_t* type_token) {
    if (!command || !type_token) return ERROR_INVALID_PARAMETER;
    *type_token = slot<Type>(command, 9)(reinterpret_cast<void*>(command));
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3CommandValidate(uint64_t command, uint32_t* valid) {
    return PdxV3CommandValidateReason(command, nullptr, valid);
}
DWORD WINAPI PdxV3CommandValidateReason(uint64_t command, void* native_reason, uint32_t* valid) {
    if (!command || !valid) return ERROR_INVALID_PARAMETER;
    *valid = slot<Validate>(command, 6)(reinterpret_cast<void*>(command), native_reason) != 0;
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3CommandValidateText(uint64_t command, void* bytes, uint32_t capacity,
    uint32_t* size, uint32_t* valid) {
    if (!command || (!bytes && capacity) || !size || !valid) return ERROR_INVALID_PARAMETER;
    *size = 0; *valid = 0;
    struct String {
        uint64_t storage[2], length, capacity;
    };
    static_assert(sizeof(String) == 32 && offsetof(String, length) == 16
        && offsetof(String, capacity) == 24);
    struct OwnedReason {
        String value = {};
        OwnedReason() {
            using Construct = void* (__fastcall*)(String*, const char*, uint64_t);
            reinterpret_cast<Construct>(base() + 0x61c420)(&value, "", 0);
        }
        ~OwnedReason() { reinterpret_cast<Dtor>(base() + 0x61f810)(&value); }
    };
    try {
        OwnedReason reason;
        // Call the C++ function pointer inside this ownership scope. /EHsc
        // assumes extern-C exports do not throw, so do not route unwind through
        // the exported raw wrapper while a temporary CString is live.
        *valid = slot<Validate>(command, 6)(reinterpret_cast<void*>(command), &reason.value) != 0;
        if (reason.value.length > UINT32_MAX) return ERROR_ARITHMETIC_OVERFLOW;
        *size = static_cast<uint32_t>(reason.value.length);
        const auto copied = *size < capacity ? *size : capacity;
        const void* data = reason.value.capacity > 15
            ? reinterpret_cast<const void*>(reason.value.storage[0]) : reason.value.storage;
        if (copied) std::memcpy(bytes, data, copied);
        return *size > capacity ? ERROR_INSUFFICIENT_BUFFER : ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandSubmit(uint64_t* owned, uint32_t flags, uint32_t* accepted) {
    if (!owned || !*owned || !accepted) return ERROR_INVALID_PARAMETER;
    auto* session = reinterpret_cast<void*>(base() + session_rva);
    if (!field<void*>(session, 0x3d8)) return ERROR_NOT_READY;
    void* command = reinterpret_cast<void*>(*owned);
    *owned = 0; *accepted = 0;
    // Session receiver consumes on acceptance AND rejection. An exception is
    // delivery-unknown; never return the old pointer as owned or auto-replay it.
    try {
        *accepted = reinterpret_cast<Receive>(base() + 0x3380c30)(session, &command, flags) != 0;
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
uint64_t WINAPI PdxV3CommandTokenName(uint32_t type_token) {
    if (!*reinterpret_cast<void**>(base() + token_registry_rva)) return 0;
    return reinterpret_cast<uint64_t>(reinterpret_cast<TokenName>(base() + 0x3b4e440)(type_token));
}
DWORD WINAPI PdxV3CommandTokenNameText(uint32_t type_token, void* bytes,
    uint32_t capacity, uint32_t* size) {
    if ((!bytes && capacity) || !size) return ERROR_INVALID_PARAMETER;
    *size = 0;
    void* registry = *reinterpret_cast<void**>(base() + token_registry_rva);
    if (!registry) return ERROR_NOT_READY;
    try {
        TokenReadLock lock(registry);
        // Copy under one read lock, not a nested call to the raw helper (a
        // queued writer could otherwise hold bit0 while waiting for this reader).
        // These exact table/bound fields are from engine TokenName3b4e440.
        const auto bound = field<int32_t>(registry, 0x54);
        if (type_token > INT32_MAX || static_cast<int32_t>(type_token) > bound)
            return ERROR_NOT_FOUND;
        auto* names = field<void**>(registry, 0x48);
        auto* name = names[type_token];
        if (!name) return ERROR_NOT_FOUND;
        const auto length = field<uint64_t>(name, 0x10);
        if (length > UINT32_MAX) return ERROR_ARITHMETIC_OVERFLOW;
        *size = static_cast<uint32_t>(length);
        const auto copied = *size < capacity ? *size : capacity;
        const void* data = field<uint64_t>(name, 0x18) > 15
            ? field<const void*>(name, 0) : name;
        if (copied) std::memcpy(bytes, data, copied);
        return *size > capacity ? ERROR_INSUFFICIENT_BUFFER : ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandResolveToken(const char* name, uint32_t name_bytes, uint32_t* type_token) {
    if (!type_token || !name || name_bytes > INT32_MAX) return ERROR_INVALID_PARAMETER;
    *type_token = UINT32_MAX;
    void* registry = *reinterpret_cast<void**>(base() + token_registry_rva);
    if (!registry) return ERROR_NOT_READY;
    struct StringView { const char* data; int32_t bytes; uint8_t is_null; uint8_t padding[3]; };
    static_assert(sizeof(StringView) == 16);
    const StringView view = {name, static_cast<int32_t>(name_bytes), 0, {}};
    using Lookup = void** (__fastcall*)(void*, void**, const StringView*);
    try {
        TokenReadLock lock(registry);
        void* node = nullptr;
        // Direct CBinLexer native lookup: 32B hash entry has sentinel+4 and ID+24.
        reinterpret_cast<Lookup>(base() + 0x3b7a920)(static_cast<unsigned char*>(registry) + 8, &node, &view);
        if (field<uint8_t>(node, 4) == 0xff) return ERROR_NOT_FOUND;
        *type_token = field<uint32_t>(node, 0x18);
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandRead(uint64_t command, void* native_reader) {
    if (!command || !native_reader) return ERROR_INVALID_PARAMETER;
    slot<PersistentIo>(command, 3)(reinterpret_cast<void*>(command), native_reader);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3CommandWrite(uint64_t command, void* native_writer) {
    if (!command || !native_writer) return ERROR_INVALID_PARAMETER;
    slot<PersistentIo>(command, 1)(reinterpret_cast<void*>(command), native_writer);
    return ERROR_SUCCESS;
}
DWORD WINAPI PdxV3CommandDeserialize(const void* bytes, uint32_t size, uint64_t* owned) {
    if (!owned || *owned || !bytes || size < 8 || size > INT32_MAX) return ERROR_INVALID_PARAMETER;
    uint16_t head[3]; std::memcpy(head, bytes, sizeof(head));
    if (head[1] != 1 || head[2] != 3) return ERROR_INVALID_DATA;
    try {
        File file(const_cast<void*>(bytes), size);
        // Engine 3371160 creates via the real registered factory then primary
        // slot3/CReader; all strings, refs and typed containers stay native.
        using Deserialize = void* (__fastcall*)(void*);
        *owned = reinterpret_cast<uint64_t>(reinterpret_cast<Deserialize>(base() + 0x3371160)(file.bytes));
        return *owned ? ERROR_SUCCESS : ERROR_INVALID_DATA;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandSerialize(uint64_t command, void* bytes, uint32_t capacity, uint32_t* size) {
    if (!command || (!bytes && capacity) || !size || capacity > INT32_MAX) return ERROR_INVALID_PARAMETER;
    *size = 0;
    try {
        struct Vector {
            alignas(16) unsigned char bytes[24] = {};
            Vector() { reinterpret_cast<Dtor>(base() + 0x61c180)(bytes); }
            ~Vector() {
                void* data = field<void*>(bytes, 0);
                if (data) {
                    field<uint32_t>(bytes, 0x0c) = 0;
                    auto* allocator = field<void*>(bytes, 0x10);
                    using Free = void (__fastcall*)(void*, void*);
                    auto table = *reinterpret_cast<uintptr_t**>(allocator);
                    reinterpret_cast<Free>(table[2])(allocator, data);
                }
            }
        } vector;
        File file(vector.bytes);
        Writer writer(file.bytes);
        uint32_t type_token = 0;
        PdxV3CommandType(command, &type_token);
        token(&writer, type_token); token(&writer, 1);
        PdxV3CommandWrite(command, &writer);
        token(&writer, 16);
        *size = field<uint32_t>(vector.bytes, 0x0c);
        if (*size > capacity) return ERROR_INSUFFICIENT_BUFFER;
        std::memcpy(bytes, field<void*>(vector.bytes, 0), *size);
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_UNHANDLED_EXCEPTION; }
}
DWORD WINAPI PdxV3CommandEnumerate(PdxV3CommandRegistration* entries, uint32_t capacity, uint32_t* count) {
    if (!count || (!entries && capacity)) return ERROR_INVALID_PARAMETER;
    *count = 0;
    for (const auto& value : registrations) {
        void* factory = live_factory(value);
        if (!factory) continue;
        if (*count < capacity) {
            auto& entry = entries[*count]; entry = {};
            entry.type_token = value.token; entry.factory_index = value.index;
            entry.factory = reinterpret_cast<uint64_t>(factory);
            auto* name = reinterpret_cast<TokenName>(base() + 0x3b4e440)(value.token);
            const char* text = field<uint64_t>(name, 0x18) > 15 ? field<const char*>(name, 0)
                : static_cast<const char*>(name);
            const auto length = field<uint32_t>(name, 0x10);
            if (text) std::memcpy(entry.token_name, text, length < 127 ? length : 127);
        }
        ++*count;
    }
    return *count > capacity ? ERROR_MORE_DATA : ERROR_SUCCESS;
}
void WINAPI PdxV3CommandInterface(void* application, void* opaque) { callback(application, nullptr, opaque); }
void WINAPI PdxV3CommandSession(void* session, void* opaque) { callback(nullptr, session, opaque); }
