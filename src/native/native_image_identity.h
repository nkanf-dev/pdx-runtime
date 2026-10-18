#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

// Game-independent PE identity and immutable-byte validation. The caller owns
// the loaded image lifetime and supplies every expected value and exact-read
// callback. No process discovery, native pointer lease, game profile or Win32
// memory-access implementation lives here. This is not a full-image hash.
namespace pdx::native_image_identity {
enum class Error { ok, invalid_descriptor, overflow, read_failed, identity_mismatch, anchor_mismatch };
struct Anchor { uintptr_t rva; const unsigned char* bytes; size_t size; };
struct Descriptor {
    uint32_t pe_offset, timestamp, image_size;
    uint16_t machine, sections, optional_magic, optional_header_bytes;
    uint64_t preferred_base;
    const Anchor* anchors;
    size_t anchor_count;
};
namespace detail {
inline uint16_t u16(const unsigned char* p) {
    return static_cast<uint16_t>(p[0] | uint16_t(p[1]) << 8);
}
inline uint32_t u32(const unsigned char* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
inline uint64_t u64(const unsigned char* p) { return u32(p) | uint64_t(u32(p + 4)) << 32; }
inline bool range(uint64_t offset, uint64_t bytes, uint64_t size) { return offset <= size && bytes <= size - offset; }
}

template<class Read>
Error verify(uintptr_t image, const Descriptor& expected, Read read) {
    // PE format offsets/signatures below are platform facts. Target-specific
    // machine/magic/header identity and every anchor remain injected data.
    constexpr size_t dos_bytes = 64, file_bytes = 24, optional_bytes = 64, chunk_bytes = 256;
    if (!image || !expected.image_size || expected.pe_offset < dos_bytes
        || !expected.machine || !expected.sections
        || (expected.optional_magic != 0x10b && expected.optional_magic != 0x20b)
        || expected.optional_header_bytes < optional_bytes
        || !detail::range(0, dos_bytes, expected.image_size)
        || !detail::range(expected.pe_offset, file_bytes + uint64_t(expected.optional_header_bytes), expected.image_size)
        || (expected.anchor_count && !expected.anchors)) return Error::invalid_descriptor;
    if (expected.image_size > std::numeric_limits<uintptr_t>::max() - image) return Error::overflow;
    for (size_t index = 0; index < expected.anchor_count; ++index) {
        const auto& anchor = expected.anchors[index];
        if (!anchor.bytes || !anchor.size || !detail::range(anchor.rva, anchor.size, expected.image_size))
            return Error::invalid_descriptor;
    }
    std::array<unsigned char, dos_bytes> dos{};
    if (!read(image, dos.data(), dos.size())) return Error::read_failed;
    if (detail::u16(dos.data()) != 0x5a4d || detail::u32(dos.data() + 0x3c) != expected.pe_offset)
        return Error::identity_mismatch;
    std::array<unsigned char, file_bytes> file{};
    if (!read(image + expected.pe_offset, file.data(), file.size())) return Error::read_failed;
    if (detail::u32(file.data()) != 0x4550 || detail::u16(file.data() + 4) != expected.machine
        || detail::u16(file.data() + 6) != expected.sections || detail::u32(file.data() + 8) != expected.timestamp
        || detail::u16(file.data() + 20) != expected.optional_header_bytes) return Error::identity_mismatch;
    std::array<unsigned char, optional_bytes> optional{};
    if (!read(image + expected.pe_offset + file_bytes, optional.data(), optional.size())) return Error::read_failed;
    if (detail::u16(optional.data()) != expected.optional_magic
        || detail::u32(optional.data() + 56) != expected.image_size) return Error::identity_mismatch;
    const auto preferred = expected.optional_magic == 0x20b ? detail::u64(optional.data() + 24)
        : uint64_t(detail::u32(optional.data() + 28));
    if (preferred != expected.preferred_base) return Error::identity_mismatch;
    std::array<unsigned char, chunk_bytes> bytes{};
    for (size_t index = 0; index < expected.anchor_count; ++index) {
        const auto& anchor = expected.anchors[index];
        for (size_t done = 0; done < anchor.size;) {
            const auto amount = std::min(bytes.size(), anchor.size - done);
            if (!read(image + anchor.rva + done, bytes.data(), amount)) return Error::read_failed;
            if (std::memcmp(bytes.data(), anchor.bytes + done, amount)) return Error::anchor_mismatch;
            done += amount;
        }
    }
    return Error::ok;
}
}
