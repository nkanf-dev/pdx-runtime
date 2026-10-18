#pragma once
#include "v3_data.h"
#include "owned_context_core.h"
#include <cstddef>
#include <cstdint>
#include <cstring>

// Internal V3 adapter only. The caller verifies the image, original source
// owner/world, and execution scope before entry. Publication remains in the
// existing ValueNode / BindingOwner / copy_any owner graph.
namespace pdx::v3_owned_context {
using ReleaseWrapper = void (*)(std::uintptr_t); // Existing release_wrapper; must not throw.
namespace abi {
inline constexpr std::uintptr_t allocate = 0x417d960;
inline constexpr std::uintptr_t free_raw = 0x417d850;
inline constexpr std::size_t refcount = 8; // These exact V3 classes, not a shared-core fact.
inline constexpr std::size_t release_size = 0x98;
inline constexpr std::uintptr_t release_ctor = 0x21c3150;
inline constexpr std::uintptr_t creation_any_to_wrapper = 0x1f66160;
inline constexpr std::size_t creation_domain = 0x10; // Exact CDataCountryCreation only.
inline constexpr char release_type_name[] = "ReleaseCountryWindow";
inline constexpr std::size_t bloc_size = 0x2f8;
inline constexpr std::uintptr_t bloc_ctor = 0x22c06e0;
inline constexpr std::uintptr_t bloc_database = 0x5966788;
inline constexpr std::uintptr_t bloc_fallback = 0x5964ba8;
inline constexpr std::uintptr_t bloc_resolver = 0xc5a400;
inline constexpr std::size_t bloc_full_id = 8;
inline constexpr std::uintptr_t bloc_existing_config = 0x22c1ae0;
inline constexpr std::uintptr_t bloc_formation_any_to_object = 0x22c8370;
inline constexpr std::uintptr_t bloc_formation_config = 0x22c1ec0;
inline constexpr char bloc_type_name[] = "PowerBlocCustomizationPopup";
inline constexpr std::size_t military_formation_size = 0x120;
inline constexpr std::uintptr_t military_formation_any_to_wrapper = 0x1b112b0;
inline constexpr std::size_t military_formation_ref = 0x10; // Exact CDataMilitaryFormation only.
inline constexpr std::uintptr_t military_formation_database = 0x5966790;
inline constexpr std::uintptr_t military_formation_fallback = 0x5964b20;
inline constexpr std::uintptr_t military_formation_resolver = 0xc59d00;
inline constexpr std::uintptr_t military_formation_ctor = 0x22a6b70;
inline constexpr std::uintptr_t military_formation_initialize = 0x22a76b0;
inline constexpr char military_formation_type_name[] = "MilitaryFormationChangeNamePopup";
inline constexpr std::uintptr_t ship_any_to_wrapper = 0x1cffb10;
inline constexpr std::size_t ship_complete_ref = 0x10; // Exact CDataShip only.
inline constexpr std::uintptr_t ship_wrapper_cache = 0x59685d8;
inline constexpr std::uintptr_t ship_database = 0x5966780;
inline constexpr std::uintptr_t ship_fallback = 0x5964920;
inline constexpr std::uintptr_t ship_resolver = 0xcc1530; // NOT wrapper-cache getter 0x10a8670.
inline constexpr std::uintptr_t ship_name_factory = 0x21cf450;
inline constexpr std::uintptr_t ship_name_initialize = 0x2294f10;
inline constexpr char ship_name_type_name[] = "ShipChangeNamePopup";
} // namespace abi

inline void retain_adapter_ref(std::uintptr_t object) noexcept {
    InterlockedIncrement(reinterpret_cast<volatile LONG*>(object + abi::refcount));
}

namespace detail {
template<class T>
T read(std::uintptr_t address, std::size_t offset = 0) noexcept {
    T value;
    std::memcpy(&value, reinterpret_cast<const void*>(address + offset), sizeof(value));
    return value;
}
template<class Construct, class Configure>
DWORD construct(std::uintptr_t image, std::size_t bytes, Construct&& ctor,
                Configure&& configure, ReleaseWrapper release_wrapper,
                std::uintptr_t* adapter_ref) {
    void* object = nullptr;
    const DWORD status = owned_context::allocate_construct(
        [=]() -> void* {
            using Allocate = void* (__fastcall*)(std::uint64_t);
            return reinterpret_cast<Allocate>(image + abi::allocate)(bytes);
        },
        [=](void* raw) noexcept {
            using Free = void (__fastcall*)(void*);
            reinterpret_cast<Free>(image + abi::free_raw)(raw);
        }, ctor,
        [](void* value) noexcept { retain_adapter_ref(reinterpret_cast<std::uintptr_t>(value)); },
        [=](void* value) noexcept { release_wrapper(reinterpret_cast<std::uintptr_t>(value)); },
        configure, DWORD(ERROR_SUCCESS), DWORD(ERROR_NOT_ENOUGH_MEMORY), object);
    *adapter_ref = reinterpret_cast<std::uintptr_t>(object);
    return status;
}
} // namespace detail

// Original retained CPdxPtr<CDataCountryCreation> Any only. The caller pins its
// original source owner, not a retagged raw wrapper or a reconstructed Any blob.
// Success transfers one adapter ref. Failure leaves output zero; C++ exceptions
// propagate after the phase-appropriate cleanup to the caller's error boundary.
inline DWORD release_country_from_owned_any(
    std::uintptr_t image, const PdxV3DataAny& original_owned_any,
    ReleaseWrapper release_wrapper, std::uintptr_t* adapter_ref) {
    if (!adapter_ref) return ERROR_INVALID_PARAMETER;
    *adapter_ref = 0;
    if (!release_wrapper) return ERROR_INVALID_PARAMETER;
    using FromAny = bool (__fastcall*)(void*, void**);
    void* wrapper = nullptr;
    // Native converter only reads the Any; const_cast reflects its native ABI.
    if (!reinterpret_cast<FromAny>(image + abi::creation_any_to_wrapper)(
            const_cast<unsigned char*>(original_owned_any.bytes), &wrapper) || !wrapper)
        return ERROR_INVALID_DATA;
    const auto creation = detail::read<std::uintptr_t>(
        reinterpret_cast<std::uintptr_t>(wrapper), abi::creation_domain);
    if (!creation) return ERROR_INVALID_DATA;
    return detail::construct(image, abi::release_size,
        [=](void* storage) {
            using Ctor = void* (__fastcall*)(void*, const void*);
            reinterpret_cast<Ctor>(image + abi::release_ctor)(
                storage, reinterpret_cast<const void*>(creation));
        }, [](void*) -> DWORD { return ERROR_SUCCESS; }, release_wrapper, adapter_ref);
}

// Complete ID, not low24 slot. The parent may derive it from the original owned
// Bloc Any via the exact native converter and its proved +0x10 complete-ID field.
inline DWORD bloc_existing_from_full_id(
    std::uintptr_t image, std::uint32_t complete_id,
    ReleaseWrapper release_wrapper, std::uintptr_t* adapter_ref) {
    if (!adapter_ref) return ERROR_INVALID_PARAMETER;
    *adapter_ref = 0;
    if (!release_wrapper) return ERROR_INVALID_PARAMETER;
    if (!detail::read<std::uintptr_t>(image, abi::bloc_database)) return ERROR_NOT_READY;
    using Resolve = std::uintptr_t (__fastcall*)(const std::uint32_t*);
    const auto bloc = reinterpret_cast<Resolve>(image + abi::bloc_resolver)(&complete_id);
    if (!bloc || bloc == detail::read<std::uintptr_t>(image, abi::bloc_fallback)
        || detail::read<std::uint32_t>(bloc, abi::bloc_full_id) != complete_id)
        return ERROR_NOT_FOUND;
    return detail::construct(image, abi::bloc_size,
        [=](void* storage) {
            using Ctor = void* (__fastcall*)(void*);
            reinterpret_cast<Ctor>(image + abi::bloc_ctor)(storage);
        },
        [=](void* object) -> DWORD {
            using Configure = void (__fastcall*)(void*, void*);
            reinterpret_cast<Configure>(image + abi::bloc_existing_config)(
                object, reinterpret_cast<void*>(bloc));
            return ERROR_SUCCESS;
        }, release_wrapper, adapter_ref);
}
// Original retained CPdxPtr<CPowerBlocFormation> Any; fromAny returns a borrowed
// formation domain object directly, NOT a CDataMilitaryFormation wrapper.
// A configure throw is not rollback: the outer popup-ref guard cleans the actual
// native owner graph, including popup+0xc0 if native configure installed it.
inline DWORD bloc_formation_from_owned_any(
    std::uintptr_t image, const PdxV3DataAny& original_owned_any,
    ReleaseWrapper release_wrapper, std::uintptr_t* adapter_ref) {
    if (!adapter_ref) return ERROR_INVALID_PARAMETER;
    *adapter_ref = 0;
    if (!release_wrapper) return ERROR_INVALID_PARAMETER;
    using FromAny = bool (__fastcall*)(void*, void**);
    void* formation = nullptr;
    if (!reinterpret_cast<FromAny>(image + abi::bloc_formation_any_to_object)(
            const_cast<unsigned char*>(original_owned_any.bytes), &formation) || !formation)
        return ERROR_INVALID_DATA;
    return detail::construct(image, abi::bloc_size,
        [=](void* storage) {
            using Ctor = void* (__fastcall*)(void*);
            reinterpret_cast<Ctor>(image + abi::bloc_ctor)(storage);
        },
        [=](void* object) -> DWORD {
            void* consumed_cell = formation;
            retain_adapter_ref(reinterpret_cast<std::uintptr_t>(formation));
            // Callee owns this retained cell on entry, normal return AND throw
            // (EH4 state0 action 0x76ed10). No caller RAII or post-call release.
            // Its nonzero tail bytes do not imply a live caller-owned ref.
            using Configure = void (__fastcall*)(void*, void**);
            reinterpret_cast<Configure>(image + abi::bloc_formation_config)(object, &consumed_cell);
            return ERROR_SUCCESS;
        }, release_wrapper, adapter_ref);
}

// Original retained CPdxPtr<CDataMilitaryFormation> Any, not PowerBlocFormation.
// Initialize is mandatory: it copies the current native name, flag index and
// colors into the complete GUI-null context. No Show/manager operation is used.
inline DWORD military_formation_from_owned_any(
    std::uintptr_t image, const PdxV3DataAny& original_owned_any,
    ReleaseWrapper release_wrapper, std::uintptr_t* adapter_ref) {
    if (!adapter_ref) return ERROR_INVALID_PARAMETER;
    *adapter_ref = 0;
    if (!release_wrapper) return ERROR_INVALID_PARAMETER;
    using FromAny = bool (__fastcall*)(void*, void**);
    void* wrapper = nullptr;
    if (!reinterpret_cast<FromAny>(image + abi::military_formation_any_to_wrapper)(
            const_cast<unsigned char*>(original_owned_any.bytes), &wrapper) || !wrapper)
        return ERROR_INVALID_DATA;
    const auto complete_ref = detail::read<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(wrapper), abi::military_formation_ref);
    if (complete_ref == UINT32_MAX) return ERROR_NOT_FOUND;
    if (!detail::read<std::uintptr_t>(image, abi::military_formation_database))
        return ERROR_NOT_READY;
    using Resolve = std::uintptr_t (__fastcall*)(const std::uint32_t*);
    const auto formation = reinterpret_cast<Resolve>(image + abi::military_formation_resolver)(
        &complete_ref);
    if (!formation || formation == detail::read<std::uintptr_t>(image, abi::military_formation_fallback)
        || detail::read<std::uint32_t>(formation, 8) != complete_ref) return ERROR_NOT_FOUND;
    return detail::construct(image, abi::military_formation_size,
        [=](void* storage) {
            using Ctor = void* (__fastcall*)(void*, std::uint32_t);
            reinterpret_cast<Ctor>(image + abi::military_formation_ctor)(storage, complete_ref);
        },
        [=](void* object) -> DWORD {
            using Initialize = void (__fastcall*)(void*);
            reinterpret_cast<Initialize>(image + abi::military_formation_initialize)(object);
            return ERROR_SUCCESS;
        }, release_wrapper, adapter_ref);
}

// Genuine CPdxPtr<CDataShip> source only. The original factory owns its internal
// allocation/partial construction; this is NOT an in-place allocate+ctor ABI.
// Normal return exposes a complete exclusive ref0 object. Existing phase logic
// establishes ref1 before native Initialize or publication can fail.
inline DWORD ship_name_from_owned_any(
    std::uintptr_t image, const PdxV3DataAny& original_owned_any,
    ReleaseWrapper release_wrapper, std::uintptr_t* adapter_ref) {
    if (!adapter_ref) return ERROR_INVALID_PARAMETER;
    *adapter_ref = 0;
    if (!release_wrapper) return ERROR_INVALID_PARAMETER;
    using FromAny = bool (__fastcall*)(void*, void**);
    void* wrapper = nullptr;
    if (!reinterpret_cast<FromAny>(image + abi::ship_any_to_wrapper)(
            const_cast<unsigned char*>(original_owned_any.bytes), &wrapper) || !wrapper)
        return ERROR_INVALID_DATA;
    const auto complete_ref = detail::read<std::uint32_t>(
        reinterpret_cast<std::uintptr_t>(wrapper), abi::ship_complete_ref);
    if (complete_ref == UINT32_MAX) return ERROR_NOT_FOUND;
    // Original Initialize reaches BOTH the wrapper cache and Ship database.
    // These are native dereference preconditions, not UI/actor/receipt policy.
    if (!detail::read<std::uintptr_t>(image, abi::ship_wrapper_cache)
        || !detail::read<std::uintptr_t>(image, abi::ship_database)) return ERROR_NOT_READY;
    using Resolve = std::uintptr_t (__fastcall*)(const std::uint32_t*);
    const auto ship = reinterpret_cast<Resolve>(image + abi::ship_resolver)(&complete_ref);
    if (!ship || ship == detail::read<std::uintptr_t>(image, abi::ship_fallback)
        || detail::read<std::uint32_t>(ship, 8) != complete_ref) return ERROR_NOT_FOUND;
    void* object = nullptr;
    const DWORD status = owned_context::from_ref0_factory(
        [=]() -> void* {
            void* cell = nullptr;
            using Factory = void** (__fastcall*)(void**, const std::uint32_t*);
            reinterpret_cast<Factory>(image + abi::ship_name_factory)(&cell, &complete_ref);
            return cell;
        },
        [](void* value) noexcept { retain_adapter_ref(reinterpret_cast<std::uintptr_t>(value)); },
        [=](void* value) noexcept { release_wrapper(reinterpret_cast<std::uintptr_t>(value)); },
        [=](void* value) -> DWORD {
            using Initialize = void (__fastcall*)(void*);
            reinterpret_cast<Initialize>(image + abi::ship_name_initialize)(value);
            return ERROR_SUCCESS;
        }, DWORD(ERROR_SUCCESS), DWORD(ERROR_NOT_ENOUGH_MEMORY), object);
    *adapter_ref = reinterpret_cast<std::uintptr_t>(object);
    return status;
}
} // namespace pdx::v3_owned_context
