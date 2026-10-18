#include "../v3_data.h"
#include "../v3_data_projection_scope.h"
#include "../v3_ui_map.h"
#include "../v3_ui_dialogs.h"
#include "../pdx_runtime_bridge.h"
#include "../platform_memory_read.h"
#include "../original_source_core.h"
#include "../native_image_identity.h"
#include "../data_projection_core.h"
#include "../dependent_sequence_core.h"
#include "../v3_game_rule_selection.h"
#include "../v3_data_native_fields.h"
#include "../v3_data_original_sources.h"
#include "../v3_data_company_sources.h"
#include "../v3_data_rule_fields.h"
#include <type_traits>
#include <cstdio>
#include <vector>
#include <cstring>
#include <string>
#include <stdexcept>
#include <memory>

namespace {
int failures = 0;
int releases = 0;
DWORD release_owned(uint64_t* handle) { ++releases; *handle = 0; return ERROR_SUCCESS; }
void check(bool ok, const char* label) {
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) ++failures;
}

struct ChainAdapter {
    using Status = int;
    struct Context { int previous = -1; };
    struct Source {
        ChainAdapter* adapter = nullptr; int level = -1;
        Source() = default; Source(const Source&) = delete;
        ~Source() { if (adapter) { if (adapter->binding_live[level]) adapter->bad_lifetime = true;
            adapter->source_live[level] = false; adapter->events.push_back("S-" + std::to_string(level)); } }
    };
    struct Binding {
        ChainAdapter* adapter = nullptr; int level = -1;
        Binding() = default; Binding(const Binding&) = delete;
        ~Binding() { if (adapter) { if (!adapter->source_live[level]) adapter->bad_lifetime = true;
            adapter->binding_live[level] = false; adapter->events.push_back("B-" + std::to_string(level)); } }
    };
    std::vector<std::string> events;
    std::vector<bool> source_live{false}, binding_live{false};
    bool bad_lifetime = false, compiled = false;
    int fail_stage = 0, fail_level = -1, finish_status = 0; bool throw_finish = false;
    Status invalid_status() { return 87; }
    bool ok(Status status) { return status == 0; }
    Status compile_all(size_t depth) {
        source_live.assign(std::max<size_t>(1, depth), false); binding_live.assign(source_live.size(), false);
        events.push_back("compile"); compiled = true; return fail_stage == 1 ? 13 : 0;
    }
    Context root_context() { return {}; }
    Status produce(size_t level, Context previous, Source& source) {
        if (!compiled || (previous.previous >= 0 && (!source_live[previous.previous] || !binding_live[previous.previous]))) bad_lifetime = true;
        source.adapter = this; source.level = static_cast<int>(level); source_live[level] = true;
        events.push_back("S+" + std::to_string(level));
        return fail_stage == 2 && fail_level == static_cast<int>(level) ? 13 : 0;
    }
    Status count_and_check(size_t level, Source&) { return fail_stage == 3 && fail_level == static_cast<int>(level) ? 1237 : 0; }
    Status select(size_t level, Source&, Binding& binding) {
        if (fail_stage == 4 && fail_level == static_cast<int>(level)) return 1168;
        binding.adapter = this; binding.level = static_cast<int>(level); binding_live[level] = true;
        events.push_back("B+" + std::to_string(level)); return 0;
    }
    Context context(Source& source, Binding&) { return {source.level}; }
    Status finish(Context previous) {
        if (previous.previous >= 0 && (!source_live[previous.previous] || !binding_live[previous.previous])) bad_lifetime = true;
        events.push_back("finish"); if (throw_finish) throw std::runtime_error("owned finish unwind"); return finish_status;
    }
    bool released() const { for (size_t i = 0; i < source_live.size(); ++i) if (source_live[i] || binding_live[i]) return false; return !bad_lifetime; }
};

struct OwnedOriginalSource { uintptr_t tag = 0, member = 0; pdx::data_projection_core::PointerArrayDescriptor array{}; };
struct OriginalAdapter : ChainAdapter {
    struct Properties {};
    using Root = ChainAdapter::Source;
    using Binding = ChainAdapter::Binding;
    pdx::original_source_core::Descriptor description;
    uintptr_t root_address;
    pdx::original_source_core::View observed{};
    uint64_t start = 0, limit = 1; DWORD recorded_error = 0; unsigned consumed = 0;
    OriginalAdapter(uintptr_t object, pdx::original_source_core::Descriptor descriptor)
        : description(descriptor), root_address(object) {}
    const auto& descriptor() const { return description; }
    Status compile_all(Properties&) { return ChainAdapter::compile_all(0); }
    Status produce_root(Root& root) { return ChainAdapter::produce(0, {}, root); }
    uintptr_t root_object(const Root&) const { return root_address; }
    bool read_pointer(uintptr_t address, uintptr_t& value) { std::memcpy(&value, reinterpret_cast<void*>(address), sizeof(value)); return true; }
    bool read_array(uintptr_t address, pdx::data_projection_core::PointerArrayDescriptor& value) {
        std::memcpy(&value, reinterpret_cast<void*>(address), sizeof(value)); return true;
    }
    bool matches_tags(uintptr_t object, pdx::original_source_core::Tags tags) {
        uintptr_t value = 0; read_pointer(object, value);
        for (size_t i = 0; i < tags.count; ++i) if (value == tags.values[i]) return true;
        return false;
    }
    DWORD source_error(pdx::original_source_core::Error error) {
        using E = pdx::original_source_core::Error;
        return error == E::ok ? ERROR_SUCCESS : error == E::missing ? ERROR_NOT_READY : ERROR_INVALID_DATA;
    }
    uint64_t start_index() const { return start; }
    uint64_t item_limit() const { return limit; }
    void observe_source(const pdx::original_source_core::View& value) { observed = value; }
    DWORD stale_ordinal() { return ERROR_RETRY; }
    void observe_window(uint64_t, uint64_t, uint64_t) {}
    void begin_record(uint64_t) {}
    void record_error(DWORD status) { if (!recorded_error) recorded_error = status; }
    DWORD bind(uintptr_t, Properties&, Binding& binding) { Source unused; return ChainAdapter::select(0, unused, binding); }
    DWORD consume(uintptr_t, Binding&, Properties&) { ++consumed; return ChainAdapter::finish(Context{0}); }
    DWORD finish() { return recorded_error; }
};



struct PageFixture {
    using Status = DWORD;
    struct Source {
        PageFixture* owner = nullptr;
        ~Source() { if (owner) { if (owner->row_live) owner->bad_lifetime = true; owner->source_live = false; ++owner->source_releases; } }
    };
    struct Row {
        PageFixture* owner = nullptr;
        ~Row() { if (owner) { if (!owner->source_live) owner->bad_lifetime = true; owner->row_live = false; ++owner->row_releases; } }
    };
    bool source_live = false, row_live = false, bad_lifetime = false, compiled = false;
    size_t rows = 64, fields = 6, compilations = 0, productions = 0, selections = 0, copies = 0;
    size_t source_releases = 0, row_releases = 0;
    DWORD source_error = 0;
    std::vector<DWORD> errors;
    std::vector<std::string> copied;
    std::string long_text = std::string(150000, 'z');
    bool ok(Status status) const { return !status; }
    Status success_status() const { return 0; }
    void compile_all() { compiled = true; compilations += fields; errors.resize(rows * fields); copied.resize(rows * fields); }
    Status open(Source& source) {
        if (!compiled) bad_lifetime = true;
        source.owner = this; source_live = true; ++productions; return source_error;
    }
    size_t row_count() const { return rows; }
    size_t field_count() const { return fields; }
    Status select(Source&, size_t index, Row& row) {
        if (!source_live || row_live) bad_lifetime = true;
        row.owner = this; row_live = true; ++selections;
        return index == 2 ? ERROR_NOT_FOUND : ERROR_SUCCESS;
    }
    void copy_cell(Source&, Row&, size_t row, size_t field, Status status) {
        if (!source_live || !row_live) bad_lifetime = true;
        const auto index = row * fields + field;
        errors[index] = status ? status : field == 3 ? ERROR_INVALID_DATA : ERROR_SUCCESS;
        if (errors[index]) return;
        ++copies;
        copied[index] = field == 4 ? long_text : field == 0 ? "false" : "0";
    }
};




void test_captured_nested_lifetime() {
    namespace core = pdx::data_projection_core;
    struct Root {
        int* destructions;
        ~Root() { ++*destructions; }
    };
    struct Capture {
        std::shared_ptr<Root> original_owner;
        PdxV3DataAny any{};
    };
    struct CapturedPage : PageFixture {
        Capture* capture = nullptr; int* getter_calls = nullptr;
        PdxV3DataDirectParameterRef ref{};
        Status open(Source& source) {
            if (!capture || !capture->original_owner || *getter_calls != 1) bad_lifetime = true;
            PdxV3DataArray args{reinterpret_cast<uintptr_t>(&ref), 1, 1, 0};
            const auto status = PdxV3DataCapturedReferences(&args, {&capture->any});
            return status ? status : PageFixture::open(source);
        }
        void copy_cell(Source& source, Row& row, size_t index, size_t field, Status status) {
            if (ref.value != &capture->any || !capture->original_owner || *getter_calls != 1
                || ref.expression || ref.tag) bad_lifetime = true;
            PageFixture::copy_cell(source, row, index, field, status);
        }
    };
    struct CapturedChain : ChainAdapter {
        std::shared_ptr<Root> root;
        std::unique_ptr<Capture> captured;
        CapturedPage page;
        int getters = 0;
        Status compile_all(size_t depth) {
            const auto status = ChainAdapter::compile_all(depth);
            page.compile_all();
            for (size_t i = 0; i < depth; ++i) if (source_live[i] || binding_live[i]) bad_lifetime = true;
            captured = std::make_unique<Capture>(); captured->original_owner = root;
            ++getters; // fixture's original typed argument getter, before any borrow
            const uintptr_t original = reinterpret_cast<uintptr_t>(root.get());
            std::memcpy(captured->any.bytes + 8, &original, sizeof(original));
            page.capture = captured.get(); page.getter_calls = &getters;
            return status;
        }
        Status finish(Context) { return core::run_compiled_page(page); }
    };
    int destructions = 0;
    {
        CapturedChain chain;
        chain.root = std::shared_ptr<Root>(new Root{&destructions});
        check(core::run_scoped_chain(chain, 2) == 0 && chain.released() && !chain.page.bad_lifetime
            && chain.getters == 1 && chain.root.use_count() == 2 && !destructions,
              "typed argument captures once before borrow and original owner covers every nested source/row use");
        check(chain.page.source_releases == 1 && chain.page.row_releases == 64 && chain.captured,
              "all nested source/row cleanup finishes while original captured Any and root owner remain alive");
    }
    check(destructions == 1, "captured nested owner graph releases its final reference exactly once");
}

void test_captured_parameter_protocol() {
    PdxV3DataAny first{}, second{};
    first.bytes[0] = 0x41; first.bytes[8] = 0x72;
    second.bytes[0] = 0x43; second.bytes[8] = 0x74;
    const auto first_original = first, second_original = second;
    PdxV3DataDirectParameterRef refs[2]; std::memset(refs, 0xA5, sizeof(refs));
    PdxV3DataArray args{reinterpret_cast<uintptr_t>(refs), 2, 2, 0};
    const std::vector<const PdxV3DataAny*> captured{&first, &second};
    check(PdxV3DataCapturedReferences(&args, captured) == ERROR_SUCCESS
        && refs[0].value == &first && refs[1].value == &second
        && !refs[0].expression && !refs[0].scopes && !refs[0].arguments && !refs[0].tag
        && !refs[1].expression && !refs[1].scopes && !refs[1].arguments && !refs[1].tag,
          "production captured-ref writer supplies all native slots as direct original Any pointers without lazy programs");
    check(!std::memcmp(first.bytes, first_original.bytes, sizeof(first))
        && !std::memcmp(second.bytes, second_original.bytes, sizeof(second)),
          "installing frozen refs never edits or retags the captured typed Any");
    check(PdxV3DataCapturedReferences(&args, {&first}) == ERROR_INVALID_PARAMETER,
          "missing argument-slot storage is rejected rather than retaining a lazy slot");
    PdxV3DataProperty property{};
    PdxV3DataScope scopes[2]{{0x1000, 31, 1, {}}, {0x2000, 99, 1, {}}};
    PdxV3DataArray scope_array{reinterpret_cast<uintptr_t>(scopes), 2, 2, 0};
    int32_t index = 1; std::memcpy(property.bytes + 100, &index, sizeof(index));
    property.bytes[92] = 1;
    PdxV3DataScopeValue receiver{}; int32_t type = -1; unsigned global_calls = 0;
    PdxV3DataCapturedReceiver(&property, &scope_array, &receiver, &type,
        [&] { ++global_calls; return 77; });
    check(receiver.object == 0x2000 && !receiver.is_any && type == 99 && !global_calls,
          "production receiver initializer selects actual compiled named scope and clears scopeValue Any flag exactly like native outerPrepare");
    index = -1; std::memcpy(property.bytes + 100, &index, sizeof(index));
    PdxV3DataCapturedReceiver(&property, &scope_array, &receiver, &type,
        [&] { ++global_calls; return 77; });
    check(!receiver.object && receiver.is_any && type == 77 && global_calls == 1,
          "production receiver initializer preserves native global scope flag and type fallback");
}

void test_nested_page() {
    namespace core = pdx::data_projection_core;
    struct NestedPage : PageFixture {
        ChainAdapter* enclosing = nullptr;
        Status open(Source& source) {
            if (!enclosing || !enclosing->source_live[1] || !enclosing->binding_live[1]) bad_lifetime = true;
            return PageFixture::open(source);
        }
        void copy_cell(Source& source, Row& row, size_t index, size_t field, Status status) {
            if (!enclosing->source_live[0] || !enclosing->binding_live[0]
                || !enclosing->source_live[1] || !enclosing->binding_live[1]) bad_lifetime = true;
            PageFixture::copy_cell(source, row, index, field, status);
        }
    };
    struct NestedChain : ChainAdapter {
        NestedPage page;
        Status compile_all(size_t depth) {
            const auto status = ChainAdapter::compile_all(depth);
            page.compile_all(); page.enclosing = this;
            return status;
        }
        Status finish(Context) { return core::run_compiled_page(page); }
    } chain;
    check(core::run_scoped_chain(chain, 2) == 0 && chain.released() && !chain.page.bad_lifetime,
          "production shared scoped walker keeps all original frames around the final copied page");
    check(chain.page.compilations == chain.page.fields && chain.page.productions == 1 && chain.page.source_releases == 1,
          "nested page compiles once before borrow and produces final source once without a second evaluator");
    NestedChain failed; failed.page.source_error = ERROR_NOT_SUPPORTED;
    check(core::run_scoped_chain(failed, 2) == ERROR_NOT_SUPPORTED && failed.released()
        && failed.page.source_releases == 1 && !failed.page.row_releases,
          "nested final-source failure releases final source then enclosing original bindings/sources");
}

void test_reused_constants() {
    namespace core = pdx::data_projection_core;
    struct Payload {
        std::string text; int* destroyed;
        Payload(std::string value, int* count) : text(std::move(value)), destroyed(count) {}
        ~Payload() { ++*destroyed; }
    };
    for (const std::string text : {std::string("literal string"), std::string(150000, 'x')}) {
        int destroyed = 0, copies = 0, moves = 0;
        std::vector<std::string> snapshots;
        {
            auto compiled = std::make_shared<Payload>(text, &destroyed);
            std::shared_ptr<Payload> rows[2];
            for (auto& row : rows) {
                core::materialize_constant(true,
                    [&] { ++copies; row = compiled; },
                    [&] { ++moves; row = std::move(compiled); });
                snapshots.push_back(row->text);
            }
            check(copies == 2 && !moves && compiled && compiled.use_count() == 3,
                  "reused nontrivial constant invokes typed copy twice and leaves template owned");
            rows[0].reset(); rows[1].reset();
            check(!destroyed && compiled.use_count() == 1,
                  "each constant result releases its own reference without consuming template");
        }
        check(destroyed == 1 && snapshots[0] == text && snapshots[1] == text,
              "literal/large string snapshots survive both rows and one final template destruction");
    }
    int destroyed = 0;
    auto original = std::make_shared<Payload>("one-shot owned Any", &destroyed);
    std::shared_ptr<Payload> result;
    core::materialize_constant(false, [&] { result = original; }, [&] { result = std::move(original); });
    check(!original && result && result.use_count() == 1 && !destroyed,
          "ordinary one-shot constant retains original move semantics");
    result.reset(); check(destroyed == 1, "moved nontrivial Any releases once without leaked template ref");
}

void test_shared_page() {
    namespace core = pdx::data_projection_core;
    check(sizeof(PdxV3DataPageCell) == 80 && offsetof(PdxV3DataPageCell, binding_mode) == 20
        && offsetof(PdxV3DataPageCell, binding_kind) == 56,
          "copied provider descriptor extends cell to80B without retagging original mode");
    std::vector<unsigned char> exact;
    { const std::string original(150000, 'x'); core::append_bytes(original.data(), original.size(), exact); }
    core::append_bytes(nullptr, 0, exact);
    check(exact.size() == 150000 && exact.front() == 'x' && exact.back() == 'x',
          "production shared byte append preserves complete150k payload after original dies");
    PageFixture page;
    check(core::run_page(page) == 0 && page.productions == 1 && page.compilations == 6 && page.selections == 64,
          "page compiles each field once and produces original source once for64 rows");
    check(!page.source_live && !page.row_live && !page.bad_lifetime && page.source_releases == 1 && page.row_releases == 64,
          "all row bindings die before original page source without borrowed owner escape");
    check(page.errors[2 * 6] == ERROR_NOT_FOUND && page.errors[3] == ERROR_INVALID_DATA && page.errors[3 * 6] == 0,
          "per-row/per-field native errors preserve successful siblings and later rows");
    check(page.copied[0] == "false" && page.copied[1] == "0" && page.copied[4] == page.long_text,
          "copied false zero and150k string survive original source destruction exactly");
    PageFixture empty; empty.rows = 0;
    check(core::run_page(empty) == 0 && empty.productions == 1 && empty.source_releases == 1 && !empty.selections,
          "empty original source produces once and releases without selecting rows");
    PageFixture failed; failed.source_error = ERROR_NOT_SUPPORTED;
    check(core::run_page(failed) == ERROR_NOT_SUPPORTED && failed.source_releases == 1 && !failed.selections,
          "failed source retains exact error with cleanup and zero row getter calls");
    struct ThrowingPage : PageFixture {
        void copy_cell(Source& source, Row& row, size_t index, size_t field, Status error) {
            if (index == 4 && field == 2) throw std::runtime_error("owned synthetic cell");
            PageFixture::copy_cell(source, row, index, field, error);
        }
    } unwinding;
    bool caught = false;
    try { core::run_page(unwinding); } catch (const std::runtime_error&) { caught = true; }
    check(caught && unwinding.source_releases == 1 && unwinding.row_releases == 5
        && !unwinding.bad_lifetime && unwinding.copied[4] == unwinding.long_text,
          "page exception unwinds row then source without erasing previously copied cells");
    PdxV3DataPageRequest invalid{}; invalid.size = sizeof(invalid);
    invalid.output_address = UINT64_MAX; invalid.output_size = UINT64_MAX;
    PdxV3DataProjectPageInterface(nullptr, &invalid);
    check(invalid.error == ERROR_INVALID_PARAMETER && !invalid.output_address && !invalid.output_size,
          "actual optional page export rejects invalid dimensions before any game access");
}

void test_shared_read_preflight() {
    namespace memory = pdx::platform_memory;
    unsigned calls = 0; unsigned char output[4] = {0xA5, 0xA5, 0xA5, 0xA5}; uint32_t received = 99;
    auto counted_read = [&](uintptr_t, void* destination, uint32_t bytes) {
        ++calls; std::memset(destination, 0x77, bytes);
        return memory::ReadResult{true, bytes, ERROR_SUCCESS};
    };
    const auto overflow = memory::checked_read(UINT64_MAX, output, 2, &received, PDX_RUNTIME_MAX_RAW, counted_read);
    check(overflow == ERROR_ARITHMETIC_OVERFLOW && received == 0,
          "shared overflow preflight returns exact arithmetic error and clears nonzero byte receipt");
    check(calls == 0 && output[0] == 0xA5 && output[1] == 0xA5,
          "same production preflight invokes zero native reads and leaves output untouched for wrapped span");
    received = 99;
    check(memory::checked_read(UINT64_MAX - 1, output, 2, &received, PDX_RUNTIME_MAX_RAW, counted_read) == ERROR_ARITHMETIC_OVERFLOW
        && received == 0 && calls == 0, "half-open endpoint overflow also rejects before native read");
    received = 99;
    check(memory::checked_read(UINT64_MAX - 2, output, 2, &received, PDX_RUNTIME_MAX_RAW, counted_read) == ERROR_SUCCESS
        && received == 2 && calls == 1, "nonwrapping boundary reaches injected native operation without blanket high-address ban");
    received = 99;
    check(memory::checked_read(1, nullptr, 2, &received, PDX_RUNTIME_MAX_RAW, counted_read) == ERROR_INVALID_PARAMETER
        && received == 0 && calls == 1, "invalid destination clears receipt and performs no native read");
    received = 99;
    check(memory::checked_read(1, output, 0, &received, PDX_RUNTIME_MAX_RAW, counted_read) == ERROR_INVALID_PARAMETER
        && received == 0 && calls == 1, "zero-sized read clears receipt before native operation");
    received = 99;
    auto failed_without_code = [](uintptr_t, void*, uint32_t) { return memory::ReadResult{false, 0, ERROR_SUCCESS}; };
    check(memory::checked_read(1, output, 2, &received, PDX_RUNTIME_MAX_RAW, failed_without_code) == ERROR_PARTIAL_COPY
        && received == 0, "failed native read with zero OS error cannot report success");
    auto partial = [](uintptr_t, void*, uint32_t) { return memory::ReadResult{false, 1, ERROR_PARTIAL_COPY}; };
    check(memory::checked_read(1, output, 2, &received, PDX_RUNTIME_MAX_RAW, partial) == ERROR_PARTIAL_COPY
        && received == 1, "native partial prefix byte receipt is preserved with its error");
    auto explicit_error = [](uintptr_t, void*, uint32_t) { return memory::ReadResult{false, 1, ERROR_ACCESS_DENIED}; };
    check(memory::checked_read(1, output, 2, &received, PDX_RUNTIME_MAX_RAW, explicit_error) == ERROR_ACCESS_DENIED
        && received == 1, "native nonzero error and valid copied prefix are preserved exactly");
    auto short_success = [](uintptr_t, void*, uint32_t) { return memory::ReadResult{true, 1, ERROR_SUCCESS}; };
    check(memory::checked_read(1, output, 2, &received, PDX_RUNTIME_MAX_RAW, short_success) == ERROR_PARTIAL_COPY
        && received == 1, "short successful native operation retains prefix and reports partial copy");
    auto invalid_count = [](uintptr_t, void*, uint32_t) { return memory::ReadResult{true, 3, ERROR_SUCCESS}; };
    received = 99;
    check(memory::checked_read(1, output, 2, &received, PDX_RUNTIME_MAX_RAW, invalid_count) == ERROR_INVALID_DATA
        && received == 0, "impossible native byte count cannot be truncated into a valid receipt");
}

void test_native_image_identity() {
    namespace identity = pdx::native_image_identity;
    std::vector<unsigned char> image(2048), long_anchor(300);
    for (size_t i = 0; i < long_anchor.size(); ++i) long_anchor[i] = static_cast<unsigned char>(i);
    auto put16 = [&](size_t offset, uint16_t value) { std::memcpy(image.data() + offset, &value, sizeof(value)); };
    auto put32 = [&](size_t offset, uint32_t value) { std::memcpy(image.data() + offset, &value, sizeof(value)); };
    auto put64 = [&](size_t offset, uint64_t value) { std::memcpy(image.data() + offset, &value, sizeof(value)); };
    constexpr uint32_t pe = 0x80, optional = pe + 24;
    put16(0, 0x5a4d); put32(0x3c, pe); put32(pe, 0x4550);
    put16(pe + 4, 0x8664); put16(pe + 6, 4); put32(pe + 8, 0x12345678); put16(pe + 20, 240);
    put16(optional, 0x20b); put64(optional + 24, 0x180000000ull); put32(optional + 56, static_cast<uint32_t>(image.size()));
    std::memcpy(image.data() + 0x400, long_anchor.data(), long_anchor.size());
    const identity::Anchor anchors[] = {{0x400, long_anchor.data(), long_anchor.size()}};
    const identity::Descriptor expected{pe, 0x12345678, static_cast<uint32_t>(image.size()), 0x8664, 4, 0x20b, 240,
        0x180000000ull, anchors, 1};
    const auto base = reinterpret_cast<uintptr_t>(image.data()); size_t calls = 0, largest_read = 0;
    auto read_owned = [&](uintptr_t at, void* output, size_t bytes) {
        ++calls; largest_read = std::max(largest_read, bytes);
        if (at < base || at - base > image.size() || bytes > image.size() - (at - base)) return false;
        std::memcpy(output, image.data() + (at - base), bytes); return true;
    };
    check(identity::verify(base, expected, read_owned) == identity::Error::ok && calls == 5 && largest_read <= 256,
          "shared PE identity verifies injected owned metadata and chunked immutable anchors without game defaults");
    for (unsigned field = 0; field < 8; ++field) {
        auto changed = expected;
        if (field == 0) ++changed.pe_offset;
        if (field == 1) ++changed.timestamp;
        if (field == 2) ++changed.image_size;
        if (field == 3) ++changed.machine;
        if (field == 4) ++changed.sections;
        if (field == 5) changed.optional_magic = 0x10b;
        if (field == 6) ++changed.optional_header_bytes;
        if (field == 7) ++changed.preferred_base;
        check(identity::verify(base, changed, read_owned) == identity::Error::identity_mismatch,
              "shared PE identity rejects each mismatched expected metadata field");
    }
    image[0x400 + 299] ^= 1;
    check(identity::verify(base, expected, read_owned) == identity::Error::anchor_mismatch,
          "changed immutable anchor in a later chunk rejects image identity");
    image[0x400 + 299] ^= 1;
    calls = 0;
    auto partial_read = [&](uintptr_t at, void* output, size_t bytes) {
        if (at == base + optional) { ++calls; return false; }
        return read_owned(at, output, bytes);
    };
    check(identity::verify(base, expected, partial_read) == identity::Error::read_failed && calls == 3,
          "injected short or failed PE read cannot become a matching identity");
    auto invalid = expected; const identity::Anchor outside{image.size() - 1, long_anchor.data(), 2};
    invalid.anchors = &outside; calls = 0;
    check(identity::verify(base, invalid, read_owned) == identity::Error::invalid_descriptor && !calls,
          "out-of-image anchor rejected before any image read");
    invalid = expected; invalid.anchors = nullptr; calls = 0;
    check(identity::verify(base, invalid, read_owned) == identity::Error::invalid_descriptor && !calls,
          "missing expected anchor table rejected before image reads");
    calls = 0;
    check(identity::verify(UINTPTR_MAX - 8, expected, read_owned) == identity::Error::overflow && !calls,
          "image base plus expected extent overflow rejected before callback");
    put32(0x3c, UINT32_MAX); calls = 0;
    check(identity::verify(base, expected, read_owned) == identity::Error::identity_mismatch && calls == 1,
          "malformed actual e_lfanew cannot redirect later reads outside expected PE header");
    put32(0x3c, pe); put16(pe + 20, 16); calls = 0;
    check(identity::verify(base, expected, read_owned) == identity::Error::identity_mismatch && calls == 2,
          "truncated declared optional header rejected before optional-header read");
    put16(pe + 4, 0x14c); put16(pe + 6, 2); put32(pe + 8, 0); put16(pe + 20, 224);
    put16(optional, 0x10b); put32(optional + 28, 0);
    auto other = expected; other.machine = 0x14c; other.sections = 2; other.timestamp = 0;
    other.optional_header_bytes = 224; other.optional_magic = 0x10b; other.preferred_base = 0;
    check(identity::verify(base, other, read_owned) == identity::Error::ok,
          "shared checker accepts explicit non-AMD64 PE32 and legitimate zero expected fields");
}

void test_original_source_core() {
    namespace source = pdx::original_source_core;
    constexpr uintptr_t root_tag = 0x41, item_tag = 0x42;
    OwnedOriginalSource item; item.tag = item_tag;
    OwnedOriginalSource owner; owner.tag = root_tag; owner.member = reinterpret_cast<uintptr_t>(&item);
    const source::Descriptor singleton{{&root_tag, 1}, nullptr, 0, source::Kind::pointer_member,
        offsetof(OwnedOriginalSource, member), {&item_tag, 1}, sizeof(uintptr_t), 1};
    OriginalAdapter success{reinterpret_cast<uintptr_t>(&owner), singleton};
    check(source::run(success) == 0 && success.released() && success.consumed == 1 && success.observed.count_valid && success.observed.count == 1,
          "production original-source core selects real owned singleton with injected tags/read/bind");
    owner.member = 0;
    OriginalAdapter empty{reinterpret_cast<uintptr_t>(&owner), singleton};
    check(source::run(empty) == 0 && empty.released() && empty.observed.source_produced && empty.observed.count_valid && !empty.observed.count && !empty.consumed,
          "nullable original member proves empty without binding an absent item");
    owner.member = reinterpret_cast<uintptr_t>(&item);
    OriginalAdapter wrong{reinterpret_cast<uintptr_t>(&owner), singleton}; owner.tag = 0xBAD;
    check(source::run(wrong) == ERROR_INVALID_DATA && wrong.released() && !wrong.observed.count_valid && !wrong.consumed,
          "wrong original owner tag is unavailable, never successful empty");
    owner.tag = root_tag;
    OriginalAdapter partial{reinterpret_cast<uintptr_t>(&owner), singleton}; partial.finish_status = ERROR_MORE_DATA;
    check(source::run(partial) == ERROR_MORE_DATA && partial.released(), "original-source partial consumer destroys binding then root");
    OriginalAdapter unwind{reinterpret_cast<uintptr_t>(&owner), singleton}; unwind.throw_finish = true; bool caught = false;
    try { source::run(unwind); } catch (const std::runtime_error&) { caught = true; }
    check(caught && unwind.released(), "original-source exception destroys binding before original root");
    OriginalAdapter stale{reinterpret_cast<uintptr_t>(&owner), singleton}; stale.start = 2;
    check(source::run(stale) == ERROR_RETRY && stale.released() && !stale.consumed && stale.observed.count_valid,
          "stale copied ordinal is explicit retry, not replay or fabricated empty");
    uintptr_t elements[] = {reinterpret_cast<uintptr_t>(&item)};
    OwnedOriginalSource secondary; secondary.tag = item_tag;
    secondary.array = {reinterpret_cast<uintptr_t>(elements), 1, 1, sizeof(uintptr_t), 8};
    owner.member = reinterpret_cast<uintptr_t>(&secondary);
    const source::PointerStep steps[] = {{offsetof(OwnedOriginalSource, member), {&item_tag, 1}}};
    const source::Descriptor array{{&root_tag, 1}, steps, 1, source::Kind::pointer_array,
        offsetof(OwnedOriginalSource, array), {&item_tag, 1}, sizeof(uintptr_t), 8};
    OriginalAdapter chain{reinterpret_cast<uintptr_t>(&owner), array};
    check(source::run(chain) == 0 && chain.released() && chain.consumed == 1 && chain.observed.count == 1,
          "same original-source core traverses injected pointer chain then real owned array");
    secondary.array.capacity = 0;
    OriginalAdapter invalid{reinterpret_cast<uintptr_t>(&owner), array};
    check(source::run(invalid) == ERROR_INVALID_DATA && invalid.released() && !invalid.observed.count_valid,
          "corrupt original array descriptor is never an empty-source observation");
}

void test_shared_scope_chain() {
    namespace core = pdx::data_projection_core;
    ChainAdapter success;
    check(core::run_scoped_chain(success, 2) == 0 && success.released(), "production shared scope chain success releases all dependencies");
    check(success.events == std::vector<std::string>{"compile", "S+0", "B+0", "S+1", "B+1", "finish", "B-1", "S-1", "B-0", "S-0"},
        "all compilation precedes borrowing and cleanup is exact deepest binding/source reverse order");
    ChainAdapter partial; partial.finish_status = ERROR_MORE_DATA;
    check(core::run_scoped_chain(partial, 2) == ERROR_MORE_DATA && partial.released(), "MORE_DATA cannot retain any nested owner/binding");
    for (int phase = 1; phase <= 4; ++phase) {
        ChainAdapter failure; failure.fail_stage = phase; failure.fail_level = 1;
        check(core::run_scoped_chain(failure, 2) != 0 && failure.released(), "compile/produce/count/select failure destroys scoped ownership");
    }
    ChainAdapter unwind; unwind.throw_finish = true; bool caught = false;
    try { core::run_scoped_chain(unwind, 2); } catch (const std::runtime_error&) { caught = true; }
    check(caught && unwind.released(), "exception destroys nested dependencies without replay");
    ChainAdapter generic;
    check(core::run_scoped_chain(generic, 9) == 0 && generic.released(),
          "shared walker has no game-specific depth-eight protocol limit");
    PdxV3DataScopedProjectionRequest legacy{};
    legacy.projection.result.size = sizeof(legacy);
    legacy.projection.result.action = PDX_V3_DATA_EVALUATE_SCOPED;
    legacy.steps_offset = sizeof(legacy); legacy.steps_count = 9;
    check(PdxV3DataProjection(&legacy.projection) == ERROR_INVALID_PARAMETER,
          "legacy V3 entry retains its own depth-eight wire bound before game access");
    const size_t steps_offset = sizeof(PdxV3DataPageRequest) + sizeof(PdxV3DataPageScope) + 2 * sizeof(PdxV3DataPageSpan);
    const size_t text_offset = steps_offset + 9 * sizeof(PdxV3DataCreationStep);
    std::vector<unsigned char> storage((text_offset + 11 + 7) & ~size_t(7));
    auto* request = reinterpret_cast<PdxV3DataPageRequest*>(storage.data());
    request->size = static_cast<uint32_t>(storage.size()); request->flags = PDX_V3_DATA_PAGE_SCOPED;
    request->fields_offset = sizeof(*request) + sizeof(PdxV3DataPageScope); request->fields_count = 1;
    request->rows_offset = request->fields_offset + sizeof(PdxV3DataPageSpan); request->rows_count = 1;
    request->source_offset = static_cast<uint32_t>(text_offset); request->source_bytes = 1; request->limit = 1;
    auto* extension = reinterpret_cast<PdxV3DataPageScope*>(request + 1);
    extension->steps_offset = static_cast<uint32_t>(steps_offset); extension->steps_count = 9;
    extension->arguments_offset = static_cast<uint32_t>(text_offset);
    *reinterpret_cast<PdxV3DataPageSpan*>(storage.data() + request->fields_offset) = {static_cast<uint32_t>(text_offset + 1), 1};
    *reinterpret_cast<PdxV3DataPageSpan*>(storage.data() + request->rows_offset) = {request->size, 0};
    for (uint32_t i = 0; i < 9; ++i) {
        auto* step = reinterpret_cast<PdxV3DataCreationStep*>(storage.data() + steps_offset) + i;
        step->source_text_offset = static_cast<uint32_t>(text_offset + 2 + i); step->source_text_bytes = 1;
        step->expected_count = UINT32_MAX;
    }
    std::memset(storage.data() + text_offset, 'X', 11);
    PdxV3DataProjectPageInterface(nullptr, request);
    check(request->error == ERROR_INVALID_PARAMETER,
          "new V3 page entry retains its depth-eight wire bound independently of shared walker");
}


// Owned fixtures exercise the production generic group scheduler, not a game VM.
struct GroupFixture {
    using Status = DWORD;
    struct Source { GroupFixture* owner = nullptr; ~Source() { if (owner) { check(!owner->row_live, "group source closes after row"); owner->source_live = false; } } };
    struct Row { GroupFixture* owner = nullptr; ~Row() { if (owner) { check(owner->source_live, "group row closes before source"); owner->row_live = false; } } };
    std::vector<size_t> counts, opened;
    struct Cell { size_t source, row, field; DWORD error; };
    std::vector<Cell> cells;
    size_t source = 0, start = 0, rows = 0;
    int fail_source = -1, fail_row = -1, fatal_source = -1, fatal_row = -1;
    bool source_live = false, row_live = false, range_complete = true, finished = false;
    int compilations = 0;
    explicit GroupFixture(std::vector<size_t> values) : counts(std::move(values)) {}
    void compile_all() { ++compilations; }
    bool ok(Status status) const { return status == 0; }
    Status success_status() const { return 0; }
    void begin_source(pdx::data_projection_core::SourceCursor cursor, size_t budget) {
        check(!source_live && !row_live, "new group source never overlaps previous borrowed frame");
        source = cursor.source; start = cursor.row; rows = std::min(budget, counts[source] - start); finished = false;
    }
    Status open(Source& value) {
        value.owner = this; source_live = true; opened.push_back(source);
        return source == size_t(fail_source) ? 13 : 0;
    }
    size_t row_count() const { return rows; }
    size_t field_count() const { return 2; }
    Status select(Source&, size_t row, Row& value) {
        value.owner = this; row_live = true;
        return source == 0 && start + row == size_t(fail_row) ? 1168 : 0;
    }
    void copy_cell(Source&, Row&, size_t row, size_t field, Status status) {
        if (source == size_t(fatal_source) && start + row == size_t(fatal_row) && field == 1)
            throw std::runtime_error("owned fatal prefix");
        cells.push_back({source, start + row, field, status});
    }
    void observe_row(Status status) { if (status) range_complete = false; }
    void end_source(Status status, pdx::data_projection_core::SourceCursor cursor) {
        finished = !status && cursor.row == counts[source];
        if (status) range_complete = false;
    }
    bool source_finished() const { return finished; }
};
void test_grouped_page() {
    namespace core = pdx::data_projection_core;
    static_assert(sizeof(PdxV3DataPageRequest) == 88 && sizeof(PdxV3DataPageCell) == 80);
    static_assert(sizeof(PdxV3DataPageGroup) == 16 && sizeof(PdxV3DataPageGroupOutput) == 24);
    static_assert(sizeof(PdxV3DataSourceObservation) == 48);
    core::SourceCursor cursor{};
    struct Unclamped : GroupFixture {
        using GroupFixture::GroupFixture;
        void begin_source(core::SourceCursor position, size_t) { GroupFixture::begin_source(position, 100); }
    } unclamped({100});
    unclamped.compile_all();
    core::run_compiled_groups(unclamped, cursor, 1, 64);
    check(cursor.source == 0 && cursor.row == 64 && unclamped.cells.size() == 128,
          "shared group traversal enforces caller budget without a game-specific core limit");
    cursor = {};
    GroupFixture first({64, 1}); first.compile_all();
    check(core::run_compiled_groups(first, cursor, 2, 64) == 0 && cursor.source == 1 && cursor.row == 0
          && first.cells.size() == 128 && first.opened == std::vector<size_t>{0}, "exact 64 source tail keeps later source cursor");
    GroupFixture last({64, 1}); last.compile_all();
    check(core::run_compiled_groups(last, cursor, 2, 64) == 0 && cursor.source == 2 && last.cells.size() == 2,
          "later one-row source is not lost at previous budget boundary");
    cursor = {};
    GroupFixture mixed({20, 60, 0, 4, 3}); mixed.compile_all();
    core::run_compiled_groups(mixed, cursor, 5, 64);
    check(cursor.source == 1 && cursor.row == 44 && mixed.cells.size() == 128 && mixed.compilations == 1,
          "ordered sources share exact attempted row budget and one compilation");
    GroupFixture rest({20, 60, 0, 4, 3}); rest.compile_all();
    core::run_compiled_groups(rest, cursor, 5, 64);
    check(cursor.source == 5 && cursor.row == 0 && rest.cells.size() == 46
          && rest.opened == std::vector<size_t>{1, 2, 3, 4}, "resume consumes remainder and empty source without hiding later groups");
    cursor = {};
    GroupFixture errors({20, 60, 0, 4, 3}); errors.fail_source = 1; errors.fail_row = 3; errors.compile_all();
    core::run_compiled_groups(errors, cursor, 5, 64);
    check(cursor.source == 5 && errors.cells.size() == 54 && !errors.range_complete
          && errors.cells[6].error == 1168 && errors.cells[7].error == 1168,
          "source failure continues later sources and selection failure keeps all field errors and budget");
    cursor = {};
    GroupFixture fatal({2, 8, 3}); fatal.fatal_source = 1; fatal.fatal_row = 2; fatal.compile_all();
    bool caught = false;
    try { core::run_compiled_groups(fatal, cursor, 3, 64); } catch (const std::runtime_error&) { caught = true; }
    check(caught && cursor.source == 1 && cursor.row == 2 && fatal.cells.size() == 9
          && !fatal.source_live && !fatal.row_live && fatal.opened == std::vector<size_t>{0, 1},
          "fatal keeps copied cell prefix and unfinished cursor, unwinds owners and never opens later source");
    std::vector<unsigned char> storage(168);
    auto* r = reinterpret_cast<PdxV3DataPageRequest*>(storage.data());
    r->size = 168; r->flags = PDX_V3_DATA_PAGE_GROUPED; r->fields_offset = 128; r->fields_count = 1;
    r->rows_offset = 136; r->rows_count = 1; r->limit = 64;
    auto* scope = reinterpret_cast<PdxV3DataPageScope*>(r + 1); scope->steps_offset = scope->arguments_offset = 144;
    auto* group = reinterpret_cast<PdxV3DataPageGroup*>(scope + 1); group->sources_offset = 144; group->sources_count = 2;
    const PdxV3DataPageSpan spans[] = {{160, 1}, {168, 0}, {161, 1}, {162, 1}};
    memcpy(storage.data() + 128, spans, sizeof(spans)); memcpy(storage.data() + 160, "XAB", 3);
    PdxV3DataProjectSourcesInterface(nullptr, r);
    check(r->error == ERROR_NOT_READY, "valid grouped wire alias reaches app observation without touching any game");
    group->source_index = 3;
    PdxV3DataProjectSourcesInterface(nullptr, r);
    check(r->error == ERROR_INVALID_PARAMETER, "group cursor outside source table rejected before app access");
}



void test_action_sequence() {
    struct Adapter {
        PdxV3DataActionRequest results[3]{};
        uint32_t policy[3]{1, 1, 1};
        size_t calls = 0, returned = 0; bool stopped = false; int fail = -1;
        void invoke(size_t index) {
            ++calls; results[index].result.value_handle = 10 + index;
            if (static_cast<int>(index) == fail) throw std::runtime_error("owned sequence fault");
        }
        void completed(size_t count) { returned = count; }
        bool continue_after(size_t index) { return PdxV3DataContinueAction(results[index], policy[index]); }
        void stop() { stopped = true; }
    } rejected;
    rejected.results[0].invoked = 1;
    pdx::dependent_sequence::run(3, rejected);
    check(rejected.calls == 1 && rejected.returned == 1 && rejected.stopped,
          "rejected first action stops dependent suffix even with error0");
    Adapter no_invoke; no_invoke.results[0].accepted = 1;
    pdx::dependent_sequence::run(3, no_invoke);
    check(no_invoke.calls == 1 && no_invoke.stopped, "accepted alone without invoked does not continue");
    for (const auto flags : {std::pair<uint32_t, uint32_t>{2, 1}, {1, 2}, {2, 2}, {UINT32_MAX, 1}}) {
        Adapter noncanonical;
        noncanonical.results[0].invoked = flags.first;
        noncanonical.results[0].accepted = flags.second;
        pdx::dependent_sequence::run(3, noncanonical);
        check(noncanonical.calls == 1 && noncanonical.returned == 1 && noncanonical.stopped,
              "noncanonical original action flags never satisfy exact invoked1 accepted1 policy");
    }
    Adapter partial;
    for (auto& action : partial.results) action.invoked = action.accepted = 1;
    partial.results[0].result.error = ERROR_MORE_DATA;
    pdx::dependent_sequence::run(3, partial);
    check(partial.calls == 3 && partial.returned == 3 && !partial.stopped
          && partial.results[0].result.error == ERROR_MORE_DATA,
          "accepted original action continues through postcopy error without changing that error");
    Adapter late;
    late.results[0].invoked = late.results[0].accepted = 1;
    late.results[1].result.error = ERROR_INVALID_DATA;
    pdx::dependent_sequence::run(3, late);
    check(late.calls == 2 && late.returned == 2 && late.stopped && late.results[0].result.value_handle == 10
          && late.results[1].result.value_handle == 11 && !late.results[2].result.value_handle,
          "later failure preserves original prefix effects/handles and never invokes suffix");
    Adapter always; always.policy[0] = always.policy[1] = 0;
    pdx::dependent_sequence::run(3, always);
    check(always.calls == 3 && !always.stopped, "caller explicit always policy does not infer rejection rules");
    Adapter fault;
    fault.results[0].invoked = fault.results[0].accepted = 1; fault.fail = 1;
    bool caught = false;
    try { pdx::dependent_sequence::run(3, fault); } catch (const std::runtime_error&) { caught = true; }
    check(caught && fault.calls == 2 && fault.returned == 1 && fault.results[1].result.value_handle == 11,
          "fault keeps copied current handle beyond returned prefix and does not replay");
    PdxV3DataActionSequenceRequest invalid{}; invalid.size = sizeof(invalid); invalid.count = 33;
    check(PdxV3DataInvokeActionSequence(&invalid) == ERROR_INVALID_PARAMETER && !invalid.completed,
          "production sequence rejects invalid descriptor budget before any game access");
    check(PdxV3DataInvokeActionSequence(nullptr) == ERROR_INVALID_PARAMETER,
          "production sequence null boundary invokes no action");

    // Original InvokeAction rejects these flags/input BEFORE native factory access.
    // A wrapper that invokes even one packet therefore increments completed, which
    // distinguishes global wire rejection from the harmless original action87.
    const auto packet_size = sizeof(PdxV3DataActionRequest);
    const auto table_end = sizeof(PdxV3DataActionSequenceRequest) + 2 * sizeof(PdxV3DataActionSequenceStep);
    const auto wire = [=] {
        std::vector<unsigned char> buffer(table_end + 2 * packet_size);
        auto* sequence = reinterpret_cast<PdxV3DataActionSequenceRequest*>(buffer.data());
        sequence->size = static_cast<uint32_t>(buffer.size()); sequence->count = 2;
        sequence->steps_offset = sizeof(*sequence);
        auto* steps = reinterpret_cast<PdxV3DataActionSequenceStep*>(sequence + 1);
        for (uint32_t index = 0; index < 2; ++index) {
            steps[index].request_offset = static_cast<uint32_t>(table_end + index * packet_size);
            auto* action = reinterpret_cast<PdxV3DataActionRequest*>(buffer.data() + steps[index].request_offset);
            action->result.size = static_cast<uint32_t>(packet_size);
            action->result.action = PDX_V3_DATA_INVOKE_ACTION;
        }
        return buffer;
    };
    auto disjoint = wire();
    auto* sequence = reinterpret_cast<PdxV3DataActionSequenceRequest*>(disjoint.data());
    check(PdxV3DataInvokeActionSequence(sequence) == ERROR_INVALID_PARAMETER && sequence->completed == 2,
          "production disjoint packets preserve original action87 and original invocation order");
    for (uint32_t mode = 0; mode < 6; ++mode) {
        auto buffer = wire();
        sequence = reinterpret_cast<PdxV3DataActionSequenceRequest*>(buffer.data());
        auto* steps = reinterpret_cast<PdxV3DataActionSequenceStep*>(sequence + 1);
        auto* first = reinterpret_cast<PdxV3DataActionRequest*>(buffer.data() + steps[0].request_offset);
        auto* second = reinterpret_cast<PdxV3DataActionRequest*>(buffer.data() + steps[1].request_offset);
        if (mode == 0) steps[1].request_offset = steps[0].request_offset; // exact alias
        if (mode == 1) first->result.size += 8; // partial overlap with later header
        if (mode == 2) first->result.size += static_cast<uint32_t>(packet_size); // nested packet
        if (mode == 3) steps[1].request_offset = sequence->steps_offset; // control table
        if (mode == 4) first->result.size = sequence->size; // outside allocation
        if (mode == 5) second->result.action = PDX_V3_DATA_SNAPSHOT; // wrong original discriminator
        check(PdxV3DataInvokeActionSequence(sequence) == ERROR_INVALID_PARAMETER
              && !sequence->completed && !sequence->stopped,
              "production sequence rejects all packet/control alias and discriminator defects before any action");
    }
}



void test_prefix_arguments() {
    auto packet = [](bool grouped) {
        std::vector<unsigned char> bytes(512);
        auto* r = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        r->size = static_cast<uint32_t>(bytes.size()); r->fields_count = r->rows_count = r->limit = 1;
        r->flags = PDX_V3_DATA_PAGE_SCOPED | PDX_V3_DATA_PAGE_CAPTURE_STEP_ARGUMENTS
            | (grouped ? PDX_V3_DATA_PAGE_GROUPED : PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS);
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(r + 1);
        size_t cursor = sizeof(*r) + sizeof(*scope) + (grouped ? sizeof(PdxV3DataPageGroup) : 0);
        r->fields_offset = static_cast<uint32_t>(cursor); cursor += sizeof(PdxV3DataPageSpan);
        r->rows_offset = static_cast<uint32_t>(cursor); cursor += sizeof(PdxV3DataPageSpan);
        scope->steps_offset = static_cast<uint32_t>(cursor); scope->steps_count = 2;
        cursor += 2 * sizeof(PdxV3DataCreationStep);
        scope->arguments_offset = static_cast<uint32_t>(cursor); scope->arguments_count = grouped ? 0 : 1;
        if (!grouped) cursor += sizeof(PdxV3DataPageSpan);
        PdxV3DataPageGroup* group = grouped ? reinterpret_cast<PdxV3DataPageGroup*>(scope + 1) : nullptr;
        if (group) { group->sources_offset = static_cast<uint32_t>(cursor); group->sources_count = 1; cursor += sizeof(PdxV3DataPageSpan); }
        scope->reserved = static_cast<uint32_t>(cursor); cursor += 2 * sizeof(PdxV3DataPageSpan);
        auto* frames = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + scope->reserved);
        frames[1] = {static_cast<uint32_t>(cursor), 1}; cursor += sizeof(PdxV3DataPageSpan);
        auto text = [&](char value) { const auto offset = static_cast<uint32_t>(cursor); bytes[cursor++] = value; return PdxV3DataPageSpan{offset, 1}; };
        *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + r->fields_offset) = text('F');
        *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + r->rows_offset) = {r->size, 0};
        const auto source = text('S');
        if (group) *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + group->sources_offset) = source;
        else { r->source_offset = source.offset; r->source_bytes = source.count; }
        auto* steps = reinterpret_cast<PdxV3DataCreationStep*>(bytes.data() + scope->steps_offset);
        for (uint32_t i = 0; i < 2; ++i) {
            const auto expression = text('P' + static_cast<char>(i));
            steps[i].source_text_offset = expression.offset; steps[i].source_text_bytes = expression.count;
            steps[i].expected_count = UINT32_MAX;
        }
        if (!grouped) *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + scope->arguments_offset) = text('A');
        *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + frames[1].offset) = text('B');
        return bytes;
    };
    for (bool grouped : {false, true}) {
        auto bytes = packet(grouped); auto* r = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        PdxV3DataProjectTypedStepsInterface(nullptr, r);
        check(r->error == ERROR_NOT_READY && !r->output_address && !r->row_count,
              "production typed-step preflight accepts isolated prefix/final frames and grouped Page alias before app access");
    }
    for (uint32_t mode = 0; mode < 10; ++mode) {
        auto bytes = packet(false); auto* r = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(r + 1);
        auto* frames = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + scope->reserved);
        auto* argument = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + frames[1].offset);
        if (mode == 0) r->flags &= ~PDX_V3_DATA_PAGE_SCOPED;
        if (mode == 1) scope->reserved = 0;
        if (mode == 2) ++scope->reserved;
        if (mode == 3) frames[1].count = PDX_V3_DATA_PAGE_FIELDS + 1;
        if (mode == 4) frames[1].offset = r->size;
        if (mode == 5) argument->offset = r->size;
        if (mode == 6) argument->count = 0;
        if (mode == 7) bytes[argument->offset] = 0;
        if (mode == 8) scope->reserved = r->size - 4;
        if (mode == 9) ++frames[1].offset;
        PdxV3DataProjectTypedStepsInterface(nullptr, r);
        check(r->error == ERROR_INVALID_PARAMETER && !r->output_address && !scope->completed_steps,
              "production typed-step control/table/text bounds reject before app/factory/source borrowing");
    }
    PdxV3DataAny prefix_any{}, final_any{};
    PdxV3DataDirectParameterRef prefix_storage{}, final_storage{};
    PdxV3DataArray prefix_args{reinterpret_cast<uintptr_t>(&prefix_storage), 1, 1, 0};
    PdxV3DataArray final_args{reinterpret_cast<uintptr_t>(&final_storage), 1, 1, 0};
    check(PdxV3DataCapturedReferences(&prefix_args, {&prefix_any}) == ERROR_SUCCESS
          && PdxV3DataCapturedReferences(&final_args, {&final_any}) == ERROR_SUCCESS
          && prefix_storage.value == &prefix_any && final_storage.value == &final_any
          && prefix_storage.value != final_storage.value,
          "production direct-ref40 frames preserve independent prefix/final Any slots in owned fixture storage");
    check(PdxV3DataCapturedReferences(&prefix_args, {}) == ERROR_INVALID_PARAMETER
          && prefix_storage.value == &prefix_any,
          "production captured frame count mismatch never substitutes or overwrites another slot");
    PdxV3DataDomainTypeCache types; int lookups = 0, row_checks = 0;
    bool same_type = true;
    for (uint32_t row = 0; row < 64; ++row) {
        ++row_checks;
        same_type = same_type && types.lookup(PDX_V3_DATA_COUNTRY, [&] { ++lookups; return 17; }) == 17;
    }
    check(same_type && lookups == 1 && row_checks == 64,
          "Page callback positive type cache removes repeated domain scan but not per-row validation");
    int missing_calls = 0;
    const auto missing_first = types.lookup(PDX_V3_DATA_STATE, [&] { ++missing_calls; return -1; });
    const auto missing_second = types.lookup(PDX_V3_DATA_STATE, [&] { ++missing_calls; return -1; });
    const auto later = types.lookup(PDX_V3_DATA_STATE, [&] { ++missing_calls; return 0; });
    const auto cached_zero = types.lookup(PDX_V3_DATA_STATE, [&] { ++missing_calls; return 99; });
    check(missing_first == -1 && missing_second == -1 && later == 0 && cached_zero == 0 && missing_calls == 3,
          "Page type cache retries missing types and preserves a genuine dynamic type0");
    PdxV3DataDomainTypeCache next_callback;
    check(next_callback.lookup(PDX_V3_DATA_COUNTRY, [&] { ++lookups; return 29; }) == 29 && lookups == 2,
          "Page positive type cache has no lifetime beyond its callback owner");
    int unknown_calls = 0;
    types.lookup(UINT32_MAX, [&] { ++unknown_calls; return -1; });
    types.lookup(UINT32_MAX, [&] { ++unknown_calls; return -1; });
    check(unknown_calls == 2, "Page cache never changes original lookup behavior outside known V3 domains");
}




void test_company_source_count_observation() {
    namespace source = pdx::v3_company_sources; namespace core = pdx::original_source_core;
    constexpr uintptr_t image = 0x10000000;
    struct Database { uintptr_t tag = image + source::database_vtable_rva;
        unsigned char prefix[0x78]{}; PdxV3DataArray definitions{}; } database;
    uintptr_t entries[] = {1, 2, 3}; uintptr_t slot = reinterpret_cast<uintptr_t>(&database);
    for (unsigned mode = 0; mode < 6; ++mode) {
        database.tag = image + source::database_vtable_rva;
        slot = reinterpret_cast<uintptr_t>(&database);
        database.definitions = {reinterpret_cast<uintptr_t>(entries), 3, UINT32_MAX, 0};
        uint32_t expected = UINT32_MAX; auto error = core::Error::invalid; bool read = true;
        if (mode == 1) { database.definitions.capacity = 1; database.definitions.count = expected = 3; }
        if (mode == 2) { database.definitions = {UINTPTR_MAX - 3, 2, 2, 0}; expected = 2; error = core::Error::overflow; }
        if (mode == 3) { slot = 0; expected = 0x1234; read = false; error = core::Error::missing; }
        if (mode == 4) { database.tag = 0x1234; expected = 0x1234; read = false; error = core::Error::wrong_type; }
        if (mode == 5) { database.definitions = {}; expected = 0; error = core::Error::ok; }
        source::CountObservation observed; core::View view;
        PdxV3DataPageRequest page{}; page.source_count = 0x1234;
        const auto actual = source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view, &observed);
        // EXACT production Page.open helper returns BEFORE any row/getter
        // allocation on an invalid observed count; no fake Python header here.
        page.error = source::open_page_source(page, reinterpret_cast<uintptr_t>(&slot), image, view);
        check(actual == error && observed.read == read && page.source_count == expected
            && !page.count_valid && !page.row_count && !page.output_address
            && view.count_valid == (mode == 5) && page.error == PdxV3DataProjectionError(error),
            "actual Company Reader+Page count observation keeps read rawbits on invalid/overflow, no invented absent count or validity");
    }
}

void test_page_publication_errors() {
    for (unsigned branch = 0; branch < 3; ++branch) {
        for (DWORD primary : {DWORD(ERROR_SUCCESS), DWORD(ERROR_UNHANDLED_EXCEPTION), DWORD(ERROR_INVALID_DATA), DWORD(ERROR_IO_PENDING)}) {
            PdxV3DataPageRequest page{};
            page.error = primary; page.source_count = 130; page.count_valid = 1; page.row_count = 71;
            PdxV3DataPagePublicationBuffer output; int allocations = 0, last_errors = 0;
            const auto injected_allocate = [&](size_t) -> void* { ++allocations; return nullptr; };
            const auto injected_error = [&]() -> DWORD { ++last_errors; return ERROR_COMMITMENT_LIMIT; };
            const auto cells = branch == 0 ? SIZE_MAX / sizeof(PdxV3DataPageCell) + 1 : size_t(1);
            const auto copied = branch == 1 ? SIZE_MAX - sizeof(PdxV3DataPageCell) + 1 : size_t(1);
            const bool published = PdxV3DataPageAllocatePublication(page, 0, cells, copied,
                injected_allocate, injected_error, output);
            const DWORD failure = branch == 2 ? ERROR_COMMITMENT_LIMIT : ERROR_ARITHMETIC_OVERFLOW;
            const DWORD expected = primary && primary != ERROR_IO_PENDING ? primary : failure;
            check(!published && page.error == expected && !output.storage
                && !page.output_address && !page.output_size && !page.cells_count
                && page.source_count == 130 && page.count_valid == 1 && page.row_count == 71
                && allocations == (branch == 2 ? 1 : 0) && last_errors == (branch == 2 ? 1 : 0),
                "actual publication overflow/OOM branch preserves prior574/13, otherwise real error; counts/no bytes/no provider replay");
        }
    }
    PdxV3DataPageRequest page{}; unsigned char owned[128]{}; int allocations = 0, errors = 0;
    PdxV3DataPagePublicationBuffer output;
    check(PdxV3DataPageAllocatePublication(page, 0, 1, 4,
        [&](size_t count) -> void* { ++allocations; return count == 84 ? owned : nullptr; },
        [&]() -> DWORD { ++errors; return ERROR_NOT_ENOUGH_MEMORY; }, output)
        && !page.error && output.table_bytes == 80 && output.payload_offset == 80
        && output.bytes == 84 && output.storage == owned && allocations == 1 && !errors,
        "actual publication success keeps same Cell80/payload byte layout without reading last-error");
    check(!PdxV3DataPageAllocatePublication(page, 0, 0, 0,
        [&](size_t) -> void* { ++allocations; return owned; },
        [&]() -> DWORD { ++errors; return ERROR_NOT_ENOUGH_MEMORY; }, output)
        && !page.error && !output.storage && allocations == 1 && !errors,
        "actual empty publication keeps no allocation/no invented bytes/error");
}

void test_company_page_sources() {
    namespace source = pdx::v3_company_sources;
    namespace core = pdx::original_source_core;
    constexpr uintptr_t image = 0x10000000;
    struct Database { uintptr_t tag = image + source::database_vtable_rva;
        unsigned char prefix[0x78]{}; PdxV3DataArray definitions{}; } database;
    static_assert(offsetof(Database, definitions) == 0x80);
    uintptr_t definitions[130];
    for (size_t i = 0; i < 130; ++i) definitions[i] = 1000 + i;
    definitions[65] = 0; // Original null position, never filtered/compacted.
    database.definitions = {reinterpret_cast<uintptr_t>(definitions), 140, 130, 0};
    uintptr_t slot = reinterpret_cast<uintptr_t>(&database);
    core::View view;
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::ok
        && view.count == 130 && view.array.capacity == 140 && view.source_produced && view.count_valid,
        "Company production SLOT->tagged DB+80 resolves all130 BEFORE any filter/budget64");
    uintptr_t original = 0;
    check(source::element(view, 129, original) == core::Error::ok && original == 1129,
        "Company full domain includes final original position beyond64");
    check(source::element(view, 65, original) == core::Error::missing && !original
        && source::element(view, 66, original) == core::Error::ok && original == 1066,
        "Company null original row is explicit missing, later source index unchanged");
    size_t cells = 99;
    check(source::cell_count(130, 2, cells) && cells == 260 && source::cell_count(0, 1, cells) && !cells,
        "Company fullN cell window is130/260 or genuine empty, never64-clipped");
    check(!source::cell_count(UINT32_MAX, 2, cells) && !source::cell_count(1, 0, cells),
        "Company uint32 Cell80 count overflow is checked before allocation/getters");
    database.definitions = {}; view = {};
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::ok
        && view.source_produced && view.count_valid && !view.count,
        "Company genuine empty DB is observed empty success");
    slot = 0;
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::missing
        && !view.source_produced && !view.count_valid,
        "Company null current DB is missing source, not successful empty catalog");
    check(source::resolve(0, image, view) == core::Error::missing && !view.count_valid,
        "Company absent TLS SLOT is missing before a dereference");
    slot = reinterpret_cast<uintptr_t>(&database); database.tag = 0x1234;
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::wrong_type,
        "Company profile checks actual original DB primary tag, not a typed-looking name");
    database.tag = image + source::database_vtable_rva;
    database.definitions = {reinterpret_cast<uintptr_t>(definitions), 140, UINT32_MAX, 0}; // native signed -1 bits
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::invalid
        && view.source_produced && !view.count_valid,
        "Company signed negative native count remains an error without fabricated count");
    database.definitions = {reinterpret_cast<uintptr_t>(definitions), 1, 2, 0};
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::invalid,
        "Company actual capacity/count mismatch is not silently clipped");
    database.definitions = {UINTPTR_MAX - 3, 2, 2, 0};
    check(source::resolve(reinterpret_cast<uintptr_t>(&slot), image, view) == core::Error::overflow,
        "Company original pointer-array arithmetic overflow stops before read");
    void* produced = reinterpret_cast<void*>(0x12345678); bool exact_args = false; int factories = 0;
    const auto wrapper = source::construct_wrapper(0x9876, [&](void** out, const uintptr_t* ref) {
        ++factories; exact_args = out && !*out && ref && *ref == 0x9876; *out = produced;
    });
    check(exact_args && factories == 1 && wrapper == reinterpret_cast<uintptr_t>(produced),
        "Company real factory shape uses fresh OUTPUT CPdx cell + definition pointer-reference");
    check(!source::construct_wrapper(0x9876, [](void**, const uintptr_t*) {}),
        "Company absent completed factory output stays zero, no guessed wrapper/refcount");
    volatile bool factory_throws = true;
    try { source::construct_wrapper(0x9876, [&](void**, const uintptr_t*) {
            if (factory_throws) throw std::runtime_error("factory"); });
        check(false, "Company factory exception should propagate"); }
    catch (const std::runtime_error& e) { check(std::strcmp(e.what(), "factory") == 0,
        "Company native factory owns opaque partial unwind; original exception is not replaced"); }
    // Injected CPU getters, not native GetKey execution. This is the SAME
    // scheduler used by PageProjectionAdapter, with a full count above64.
    PageFixture full; full.rows = 130; full.fields = 2;
    check(pdx::data_projection_core::run_page(full) == ERROR_SUCCESS
        && full.compilations == 2 && full.productions == 1 && full.selections == 130
        && full.row_releases == 130 && full.source_releases == 1 && !full.bad_lifetime,
        "same Page core enumerates all130 once with one source and row-before-source cleanup");
    check(full.errors[4] == ERROR_NOT_FOUND && full.errors[5] == ERROR_NOT_FOUND
        && full.errors[258] == ERROR_SUCCESS && full.copied[259] == "0",
        "same fullN scheduler preserves original failed-row positions and successful suffix beyond64");
    struct PartialFull : PageFixture {
        void copy_cell(Source& source, Row& selected, size_t row, size_t field, Status status) {
            if (row == 70 && field == 1) throw std::runtime_error("full copied prefix");
            PageFixture::copy_cell(source, selected, row, field, status);
        }
    } partial; partial.rows = 130; partial.fields = 2;
    try { pdx::data_projection_core::run_page(partial); check(false, "full callback exception expected"); }
    catch (const std::runtime_error& error) {
        check(std::strcmp(error.what(), "full copied prefix") == 0 && partial.copied[140] == "false"
            && partial.copied[141].empty() && partial.selections == 71
            && partial.row_releases == 71 && partial.source_releases == 1 && !partial.bad_lifetime,
            "same fullN scheduler retains copied prefix on later exception, no getter replay or skipped cleanup");
    }
    PdxV3DataPageSource record{PDX_V3_DATA_SOURCE_FULL_COMPANY_TYPES, 0, 0, 0};
    check(pdx::v3_original_sources::valid_source(record, [](PdxV3DataPageSpan) { return false; }),
        "Company Source16 kind2 is explicit canonical native carrier, never DSL interpolation");
    // Actual exported Page entry validation only. Null application short-circuits
    // before all image/engine reads, so these are CPU protocol tests, not VM calls.
    for (unsigned mode = 0; mode < 12; ++mode) {
        const size_t fixed = sizeof(PdxV3DataPageRequest) + sizeof(PdxV3DataPageScope)
            + 2 * sizeof(PdxV3DataPageSpan);
        std::vector<unsigned char> bytes(fixed + 8 + sizeof(record));
        auto* page = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        page->size = static_cast<uint32_t>(bytes.size()); page->flags = PDX_V3_DATA_PAGE_ORIGINAL_SOURCES;
        page->fields_count = page->rows_count = 1; // canonical wire limit=0 FULL N
        page->fields_offset = sizeof(*page) + sizeof(PdxV3DataPageScope);
        page->rows_offset = page->fields_offset + sizeof(PdxV3DataPageSpan);
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(page + 1);
        scope->steps_offset = scope->arguments_offset = static_cast<uint32_t>(fixed);
        *reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + page->fields_offset) = {static_cast<uint32_t>(fixed), 4};
        reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + page->rows_offset)->offset = static_cast<uint32_t>(fixed + 8);
        std::memcpy(bytes.data() + fixed, "Leaf", 4);
        page->source_offset = static_cast<uint32_t>(fixed + 4); page->source_bytes = sizeof(record);
        auto current = record;
        if (mode == 1) page->limit = 64;
        if (mode == 2) page->start = 1;
        if (mode == 3) current.kind = 99;
        if (mode == 4) current.expression_offset = static_cast<uint32_t>(fixed);
        if (mode == 5) current.reserved = 1;
        if (mode == 6) page->flags |= PDX_V3_DATA_PAGE_SCOPED;
        if (mode == 7) page->flags |= PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS;
        if (mode == 8) page->flags |= PDX_V3_DATA_PAGE_GROUPED;
        if (mode == 9) page->source_bytes = sizeof(record) - 1;
        if (mode == 10) page->source_offset = page->size - 8;
        if (mode == 11) current.kind = PDX_V3_DATA_SOURCE_PRESET_STORED_SETTINGS;
        if (mode != 10) std::memcpy(bytes.data() + page->source_offset, &current, sizeof(current));
        PdxV3DataProjectCompanyTypesInterface(nullptr, page);
        check(page->error == DWORD(mode ? ERROR_INVALID_PARAMETER : ERROR_NOT_READY)
            && !page->output_address && !page->cells_count,
            mode ? "Company malformed/unsupported subset rejected before game, not reinterpreted pagination"
                 : "Company actual unaligned Source16 valid full0/0 reaches original app boundary only");
    }
}

void test_company_sources_wire(const char* path) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path, "rb") || !file) { check(false, "Company Python packet opens"); return; }
    std::fseek(file, 0, SEEK_END); const auto size = std::ftell(file); std::rewind(file);
    std::vector<unsigned char> bytes(size > 0 ? static_cast<size_t>(size) : 0);
    const auto read = bytes.empty() ? 0 : std::fread(bytes.data(), 1, bytes.size(), file); std::fclose(file);
    if (bytes.size() < sizeof(PdxV3DataPageRequest) || read != bytes.size()) { check(false, "Company full Python packet"); return; }
    auto* page = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
    check(page->size == bytes.size() && page->limit == 0 && page->start == 0,
        "actual Python Company packet carries explicit FULL0/0 unchanged");
    PdxV3DataProjectCompanyTypesInterface(nullptr, page);
    check(page->error == ERROR_NOT_READY && !page->output_address && !page->cells_count,
        "actual Python producer passes full production C++ validator before game/app boundary");
}

void test_original_page_sources() {
    namespace source = pdx::v3_original_sources;
    namespace core = pdx::original_source_core;
    check(sizeof(PdxV3DataPageSource) == 16 && PDX_V3_DATA_PAGE_ORIGINAL_SOURCES == 32,
          "original Page source is explicit Source16 without changing copied output");
    struct Preset { unsigned char prefix[0x28]{}; PdxV3DataArray settings{}; uint64_t tail = 0; } preset;
    static_assert(offsetof(Preset, settings) == 0x28);
    uintptr_t settings[] = {0x1111, 0x2222, 0x3333};
    preset.settings = {reinterpret_cast<uintptr_t>(settings), 4, 3, 0};
    uintptr_t slot = reinterpret_cast<uintptr_t>(&preset);
    core::View view;
    check(source::resolve_preset(reinterpret_cast<uintptr_t>(&slot), view) == core::Error::ok
        && view.owner == slot && view.count == 3 && view.source_produced && view.count_valid
        && view.array.capacity == 4,
          "production V3 source profile follows original value SLOT deref0 then true Array24 data/capacity/count");
    uintptr_t value = 0;
    check(source::preset_element(view, 1, value) == core::Error::ok && value == settings[1],
          "production source selects actual stride8 const pointer not slot/address/retagged Creation");
    check(source::preset_element(view, 3, value) == core::Error::missing && !value,
          "source ordinal out of current original array stays a missing observation");
    preset.settings = {}; view = {};
    check(source::resolve_preset(reinterpret_cast<uintptr_t>(&slot), view) == core::Error::ok
        && view.source_produced && view.count_valid && !view.count,
          "actual empty original preset array differs from absent source");
    slot = 0;
    check(source::resolve_preset(reinterpret_cast<uintptr_t>(&slot), view) == core::Error::missing
        && !view.source_produced && !view.count_valid,
          "null underlying preset pointer is not fabricated as successful empty");
    slot = reinterpret_cast<uintptr_t>(&preset);
    preset.settings = {reinterpret_cast<uintptr_t>(settings), 1, 3, 0};
    check(source::resolve_preset(reinterpret_cast<uintptr_t>(&slot), view) == core::Error::invalid
        && view.source_produced && !view.count_valid,
          "real capacity/count mismatch preserves produced source without false validated count");
    preset.settings = {UINTPTR_MAX - 3, 2, 2, 0};
    check(source::resolve_preset(reinterpret_cast<uintptr_t>(&slot), view) == core::Error::overflow
        && view.source_produced && !view.count_valid,
          "original source pointer arithmetic overflow is rejected before element access");
    PdxV3DataPageSource descriptor{PDX_V3_DATA_SOURCE_PRESET_STORED_SETTINGS, 0, 0, 0};
    check(source::valid_source(descriptor, [](PdxV3DataPageSpan) { return false; }),
          "native source canonical zero span is not a DSL string");
    for (unsigned mode = 0; mode < 8; ++mode) {
        const size_t fixed = sizeof(PdxV3DataPageRequest) + sizeof(PdxV3DataPageScope)
            + 2 * sizeof(PdxV3DataPageSpan) + sizeof(PdxV3DataCreationStep);
        std::vector<unsigned char> bytes(fixed + 8 + sizeof(PdxV3DataPageSource));
        auto* page = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        page->size = static_cast<uint32_t>(bytes.size());
        page->flags = PDX_V3_DATA_PAGE_SCOPED | PDX_V3_DATA_PAGE_ORIGINAL_SOURCES;
        page->fields_count = page->rows_count = page->limit = 1;
        page->fields_offset = sizeof(*page) + sizeof(PdxV3DataPageScope);
        page->rows_offset = page->fields_offset + sizeof(PdxV3DataPageSpan);
        page->source_offset = static_cast<uint32_t>(fixed + 8); page->source_bytes = sizeof(PdxV3DataPageSource);
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(page + 1);
        scope->steps_offset = page->rows_offset + sizeof(PdxV3DataPageSpan); scope->steps_count = 1;
        scope->arguments_offset = static_cast<uint32_t>(fixed);
        auto* step = reinterpret_cast<PdxV3DataCreationStep*>(bytes.data() + scope->steps_offset);
        step->source_text_offset = static_cast<uint32_t>(fixed + 4); step->source_text_bytes = 4; step->expected_count = UINT32_MAX;
        auto* field = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + page->fields_offset);
        *field = {static_cast<uint32_t>(fixed), 4};
        auto* row = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + page->rows_offset); row->offset = page->size;
        std::memcpy(bytes.data() + fixed, "LeafRoot", 8);
        auto record = descriptor;
        if (mode == 0) record.kind = 99;
        if (mode == 1) record.reserved = 1;
        if (mode == 2) record.expression_offset = static_cast<uint32_t>(fixed);
        if (mode == 3) scope->steps_count = 0;
        if (mode == 4) page->flags |= PDX_V3_DATA_PAGE_CAPTURE_ARGUMENTS;
        if (mode == 5) record = {PDX_V3_DATA_SOURCE_EXPRESSION, static_cast<uint32_t>(fixed), 4, 0};
        if (mode == 6) page->source_bytes = sizeof(record) - 1;
        std::memcpy(bytes.data() + page->source_offset, &record, sizeof(record));
        if (mode == 7) page->source_offset = page->size - 8;
        PdxV3DataProjectOriginalSourcesInterface(nullptr, page);
        check(page->error == ERROR_INVALID_PARAMETER && !page->output_address && !page->cells_count,
              "original Source16 malformed kind/reserved/span/steps/capture/allExpr/size/range fails before game access");
    }
}

void test_original_sources_wire(const char* path) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path, "rb") || !file) { check(false, "Python Source16 fixture opens"); return; }
    std::fseek(file, 0, SEEK_END); const auto count = std::ftell(file); std::rewind(file);
    std::vector<unsigned char> bytes(count > 0 ? static_cast<size_t>(count) : 0);
    const auto read = bytes.empty() ? 0 : std::fread(bytes.data(), 1, bytes.size(), file); std::fclose(file);
    if (read != bytes.size() || bytes.size() < sizeof(PdxV3DataPageRequest)) { check(false, "Source16 full fixture"); return; }
    const auto* page = reinterpret_cast<const PdxV3DataPageRequest*>(bytes.data());
    const auto text = [&](PdxV3DataPageSpan span) {
        return span.count && span.offset >= sizeof(*page) && span.offset <= bytes.size()
            && span.count <= bytes.size() - span.offset && !std::memchr(bytes.data() + span.offset, 0, span.count);
    };
    if (page->size != bytes.size() || !(page->flags & PDX_V3_DATA_PAGE_ORIGINAL_SOURCES)
        || !(page->flags & PDX_V3_DATA_PAGE_SCOPED)) { check(false, "Source16 actual Python header flags"); return; }
    uint32_t offset = page->source_offset, sources = 1;
    if (page->flags & PDX_V3_DATA_PAGE_GROUPED) {
        const auto* group = reinterpret_cast<const PdxV3DataPageGroup*>(reinterpret_cast<const PdxV3DataPageScope*>(page + 1) + 1);
        offset = group->sources_offset; sources = group->sources_count;
    }
    check(sources && offset <= bytes.size() && uint64_t(sources) * sizeof(PdxV3DataPageSource) <= bytes.size() - offset,
          "actual Python Source16 table fits original allocation");
    if (failures) return;
    bool valid = true, native = false;
    for (uint32_t i = 0; i < sources; ++i) {
        PdxV3DataPageSource source{}; std::memcpy(&source, bytes.data() + offset + size_t(i) * sizeof(source), sizeof(source));
        valid = valid && pdx::v3_original_sources::valid_source(source, text);
        native = native || source.kind == PDX_V3_DATA_SOURCE_PRESET_STORED_SETTINGS;
    }
    check(valid && native, "actual Python Source16 passes exact production profile/registered-expression validator");
}

void test_native_fields_wire(const char* path) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path, "rb") || !file) { check(false, "Python native-field wire fixture opens"); return; }
    std::fseek(file, 0, SEEK_END); const auto bytes_count = std::ftell(file); std::rewind(file);
    std::vector<unsigned char> bytes(bytes_count > 0 ? static_cast<size_t>(bytes_count) : 0);
    const auto read = bytes.empty() ? 0 : std::fread(bytes.data(), 1, bytes.size(), file);
    std::fclose(file);
    if (read != bytes.size() || bytes.size() < sizeof(PdxV3DataPageRequest)) {
        check(false, "Python native-field wire fixture has full header"); return;
    }
    const auto* page = reinterpret_cast<const PdxV3DataPageRequest*>(bytes.data());
    const auto span = [&](PdxV3DataPageSpan text) {
        return text.count && text.count <= INT32_MAX && text.offset >= sizeof(*page)
            && text.offset <= bytes.size() && text.count <= bytes.size() - text.offset
            && !std::memchr(bytes.data() + text.offset, 0, text.count);
    };
    check(page->size == bytes.size() && (page->flags & PDX_V3_DATA_PAGE_NATIVE_FIELDS)
        && page->fields_count && page->fields_count <= PDX_V3_DATA_PAGE_FIELDS
        && page->fields_offset <= bytes.size()
        && uint64_t(page->fields_count) * sizeof(PdxV3DataPageField) <= bytes.size() - page->fields_offset,
          "actual Python native-field Field16 table fits original allocation");
    if (failures) return;
    const auto* fields = reinterpret_cast<const PdxV3DataPageField*>(bytes.data() + page->fields_offset);
    check(pdx::v3_native_fields::valid_fields(fields, page->fields_count, span),
          "actual Python native-field wire passes exact production typed-table validator");
}


void test_rule_fields() {
    namespace rule = pdx::v3_rule_fields;
    alignas(8) unsigned char definition[56]{};
    uintptr_t gui_row[] = {reinterpret_cast<uintptr_t>(definition), 0xCAFEBABE};
    alignas(8) unsigned char setting[72]{};
    const auto parent = reinterpret_cast<uintptr_t>(definition);
    std::memcpy(setting + 0x40, &parent, sizeof(parent));
    PdxV3DataScope gui{reinterpret_cast<uintptr_t>(gui_row), 0, 0};
    PdxV3DataScope choice{reinterpret_cast<uintptr_t>(setting), 603, 1};
    uintptr_t actual = 0;
    check(rule::definition(gui, 1, 0, 603, &actual) == ERROR_SUCCESS && actual == parent && !gui.is_any,
        "RuleKEY actual Gui mode1 flag0 16B row[0], dynamic type0 supported, current choice untouched");
    check(rule::definition(choice, 0, 0, 603, &actual) == ERROR_SUCCESS && actual == parent && choice.is_any == 1,
        "RuleKEY actual const Setting mode0 flag1 is DIRECT pointer parent40, NOT Any/payload/retag");
    for (unsigned mode = 0; mode < 6; ++mode) {
        auto scope = mode < 3 ? gui : choice;
        uint32_t binding = mode < 3 ? 1 : 0;
        if (mode == 0 || mode == 3) scope.is_any ^= 1;
        if (mode == 1 || mode == 4) binding = 2;
        if (mode == 2 || mode == 5) scope.type_id = 999;
        check(rule::definition(scope, binding, 0, 603, &actual) == ERROR_NOT_SUPPORTED && !actual,
            "RuleKEY unknown mode/flag/type is not reinterpreted as a guessed row or Any");
    }
    auto absent = gui; absent.object = 0;
    check(rule::definition(absent, 1, 0, 603, &actual) == ERROR_NOT_FOUND && !actual,
        "RuleKEY null actual row stays missing rather than invented empty CString");
    gui_row[0] = 0;
    check(rule::definition(gui, 1, 0, 603, &actual) == ERROR_NOT_FOUND && !actual,
        "RuleKEY null original definition is not current choice/default/name");
    gui_row[0] = parent;
    for (const auto& key : {std::string(), std::string("canonical\0rule", 14), std::string(80, 'k')}) {
        for (unsigned receiver = 0; receiver < 2; ++receiver) {
            std::vector<unsigned char> heap, copied; int constructs = 0, destroys = 0, sinks = 0;
            const auto result = rule::copy_key(receiver ? choice : gui, receiver ? 0u : 1u, 0, 603,
                [&](void* target, const void* source) {
                    ++constructs;
                    if (source != definition + 0x18) throw std::runtime_error("not canonical Rule CString");
                    auto* bytes = static_cast<unsigned char*>(target);
                    const uint64_t count = key.size(), capacity = count <= 15 ? 15 : count;
                    std::memcpy(bytes + 0x10, &count, 8); std::memcpy(bytes + 0x18, &capacity, 8);
                    if (count <= 15) { if (count) std::memcpy(bytes, key.data(), size_t(count)); }
                    else { heap.assign(key.begin(), key.end()); const auto p = reinterpret_cast<uintptr_t>(heap.data());
                        std::memcpy(bytes, &p, 8); }
                },
                [&](void* target) { ++destroys; std::memset(target, 0, 32); heap.clear(); },
                [&](const void* data, uint64_t count) { ++sinks;
                    const auto* p = static_cast<const unsigned char*>(data); copied.assign(p, p + size_t(count)); });
            check(result == ERROR_SUCCESS && constructs == 1 && destroys == 1 && sinks == 1
                && std::string(copied.begin(), copied.end()) == key,
                "RuleKEY BOTH exact receiver paths copy canonical empty/NUL/heap CString bytes before known-owner cleanup");
        }
    }
    int constructors = 0, destructors = 0;
    const auto failed = rule::copy_key(gui, 1, 0, 603,
        [&](void*, const void*) { ++constructors; throw std::bad_alloc(); },
        [&](void*) { ++destructors; }, [](const void*, uint64_t) {});
    check(failed == ERROR_NOT_ENOUGH_MEMORY && constructors == 1 && !destructors,
        "RuleKEY copy failure preserves constructor error, never destroys uncompleted native CString");
    PdxV3DataPageField field{PDX_V3_DATA_FIELD_GAME_RULE_KEY, 0, 0, 0};
    check(pdx::v3_native_fields::valid_fields(&field, 1, [](PdxV3DataPageSpan) { return false; }),
        "RuleKEY explicit Field16 kind3 is canonical direct carrier, not fake GameRule.GetKey");
    for (unsigned mode = 0; mode < 4; ++mode) {
        auto current = field;
        if (mode == 0) current.kind = 4;
        if (mode == 1) current.expression_offset = 128;
        if (mode == 2) current.expression_bytes = 1;
        if (mode == 3) current.reserved = 1;
        check(!pdx::v3_native_fields::valid_fields(&current, 1, [](PdxV3DataPageSpan) { return true; }),
            "RuleKEY rejects withheld default4/receiver-program/reserved bits without inventing default semantics");
    }
}
void test_rule_fields_wire(const char* path) {
    std::FILE* file = nullptr;
    if (fopen_s(&file, path, "rb") || !file) { check(false, "RuleKEY Python fixture opens"); return; }
    std::fseek(file, 0, SEEK_END); const auto size = std::ftell(file); std::rewind(file);
    std::vector<unsigned char> bytes(size > 0 ? static_cast<size_t>(size) : 0);
    const auto read = bytes.empty() ? 0 : std::fread(bytes.data(), 1, bytes.size(), file); std::fclose(file);
    if (bytes.size() < sizeof(PdxV3DataPageRequest) || read != bytes.size()) { check(false, "RuleKEY whole Python fixture"); return; }
    auto* page = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
    bool found = false;
    if ((page->flags & PDX_V3_DATA_PAGE_NATIVE_FIELDS) && page->fields_offset <= bytes.size()
        && uint64_t(page->fields_count) * sizeof(PdxV3DataPageField) <= bytes.size() - page->fields_offset) {
        const auto* fields = reinterpret_cast<const PdxV3DataPageField*>(bytes.data() + page->fields_offset);
        for (uint32_t i = 0; i < page->fields_count; ++i) found = found || fields[i].kind == PDX_V3_DATA_FIELD_GAME_RULE_KEY;
    }
    check(found, "actual Python RuleKEY packet contains kind3 Field16, no descriptor stringification");
    PdxV3DataProjectRuleFieldsInterface(nullptr, page);
    check(page->error == ERROR_NOT_READY && !page->output_address && !page->cells_count,
        "actual Python RuleKEY producer passes full production Page validator before app/Game boundary");
}

void test_native_fields() {
    namespace native = pdx::v3_native_fields;
    check(sizeof(PdxV3DataPageField) == 16 && PDX_V3_DATA_PAGE_NATIVE_FIELDS == 16,
          "native field input ABI16 is explicit and old Page output is unchanged");
    struct KeyFixture {
        alignas(8) unsigned char wrapper[24]{}, definition[56]{};
        std::string key; std::vector<unsigned char> heap, observed;
        int constructions = 0, destructions = 0, sinks = 0;
        bool throw_ctor = false, oom_ctor = false, throw_sink = false, throw_dtor = false, invalid = false;
        uint64_t observed_count = UINT64_MAX;
        explicit KeyFixture(std::string value) : key(std::move(value)) {
            const auto pointer = reinterpret_cast<uintptr_t>(definition);
            std::memcpy(wrapper + 0x10, &pointer, sizeof(pointer));
        }
        DWORD run() {
            return native::copy_decree_type_key(reinterpret_cast<uintptr_t>(wrapper),
                [&](void* destination, const void* source) {
                    ++constructions;
                    if (source != definition + 0x18) throw std::runtime_error("wrong original key field");
                    if (oom_ctor) throw std::bad_alloc();
                    if (throw_ctor) throw std::runtime_error("constructor did not complete");
                    auto* bytes = static_cast<unsigned char*>(destination);
                    const uint64_t count = key.size(), capacity = invalid ? 0 : (count <= 15 ? 15 : count);
                    std::memcpy(bytes + 0x10, &count, 8); std::memcpy(bytes + 0x18, &capacity, 8);
                    if (count <= 15) { if (count) std::memcpy(bytes, key.data(), static_cast<size_t>(count)); }
                    else { heap.assign(key.begin(), key.end()); heap.push_back(0);
                        const auto data = reinterpret_cast<uintptr_t>(heap.data()); std::memcpy(bytes, &data, 8); }
                },
                [&](void* destination) {
                    ++destructions;
                    if (destination == wrapper || destination == definition) throw std::runtime_error("borrowed wrapper release");
                    std::vector<unsigned char>().swap(heap); std::memset(destination, 0, 32);
                    if (throw_dtor) throw std::runtime_error("cleanup failure after observed value");
                },
                [&](const void* data, uint64_t count) {
                    ++sinks; observed_count = count;
                    if (count) {
                        const auto* first = static_cast<const unsigned char*>(data);
                        observed.assign(first, first + static_cast<size_t>(count));
                    }
                    if (throw_sink) throw std::bad_alloc();
                });
        }
    };
    for (const auto& key : {std::string(), std::string("short"), std::string("fifteen-chars!!"),
                           std::string("native-key-\xE6\x94\xBF\xE4\xBB\xA4-with-long-heap-storage"),
                           std::string("raw\0key", 7)}) {
        KeyFixture fixture(key);
        check(fixture.run() == ERROR_SUCCESS && fixture.constructions == 1 && fixture.destructions == 1
            && fixture.sinks == 1 && fixture.observed_count == key.size()
            && std::string(fixture.observed.begin(), fixture.observed.end()) == key,
            "native key copy preserves exact empty/SSO/heap/UTF8/embeddedNUL bytes and destroys completed CString only");
    }
    KeyFixture failed("key"); failed.throw_ctor = true;
    check(failed.run() == ERROR_UNHANDLED_EXCEPTION && failed.constructions == 1 && !failed.destructions && !failed.sinks,
          "native key constructor exception never destroys uncompleted destination or publishes fake empty");
    KeyFixture oom("key"); oom.oom_ctor = true;
    check(oom.run() == ERROR_NOT_ENOUGH_MEMORY && !oom.destructions && !oom.sinks,
          "native key allocation failure preserves genuine construction error");
    KeyFixture copy_partial("already observed"); copy_partial.throw_sink = true;
    check(copy_partial.run() == ERROR_NOT_ENOUGH_MEMORY && copy_partial.destructions == 1
        && copy_partial.observed_count == copy_partial.key.size() && !copy_partial.observed.empty(),
          "native key partial sink error retains observed metadata and bytes with completed-owner cleanup");
    KeyFixture cleanup_partial("copied before cleanup"); cleanup_partial.throw_dtor = true;
    check(cleanup_partial.run() == ERROR_UNHANDLED_EXCEPTION && cleanup_partial.sinks == 1
        && std::string(cleanup_partial.observed.begin(), cleanup_partial.observed.end()) == cleanup_partial.key,
          "native key cleanup error cannot erase already copied value");
    KeyFixture invalid("bad"); invalid.invalid = true;
    check(invalid.run() == ERROR_INVALID_DATA && invalid.destructions == 1 && !invalid.sinks,
          "native key invalid completed CString reports data error instead of invented key");
    KeyFixture fixture("pointer");
    uintptr_t wrapper = reinterpret_cast<uintptr_t>(fixture.wrapper), decoded = 0;
    PdxV3DataAny any{}; uintptr_t token = native::decree_type_raw_any_rva;
    std::memcpy(any.bytes, &token, 8); std::memcpy(any.bytes + 8, &wrapper, 8);
    check(native::borrowed_decree_type(0, any, &decoded) == ERROR_SUCCESS && decoded == wrapper,
          "actual mutable raw Any inline payload is consumed without named rebinding or retag");
    uintptr_t payload = reinterpret_cast<uintptr_t>(&wrapper); token |= 1;
    std::memcpy(any.bytes, &token, 8); std::memcpy(any.bytes + 8, &payload, 8);
    check(native::borrowed_decree_type(0, any, &decoded) == ERROR_SUCCESS && decoded == wrapper,
          "actual mutable raw Any heap payload owns storage not the borrowed wrapper");
    token = 0x5146FC0; std::memcpy(any.bytes, &token, 8);
    check(native::borrowed_decree_type(0, any, &decoded) == ERROR_NOT_SUPPORTED && !decoded,
          "owned named singleton is not retagged into actual borrowed GetType output");
    token = native::decree_type_raw_any_rva; wrapper = 0;
    std::memcpy(any.bytes, &token, 8); std::memcpy(any.bytes + 8, &wrapper, 8);
    check(native::borrowed_decree_type(0, any, &decoded) == ERROR_SUCCESS && !decoded,
          "native raw null pointer is preserved without fabricating an empty key");
    int calls = 0;
    auto ctor = [&](void*, const void*) { ++calls; };
    auto destroy = [&](void*) { ++calls; };
    auto sink = [&](const void*, uint64_t) { ++calls; };
    check(native::copy_decree_type_key(0, ctor, destroy, sink) == ERROR_NOT_FOUND && !calls,
          "null native wrapper has no key and performs no construction or borrowed release");
    unsigned char null_definition[24]{};
    check(native::copy_decree_type_key(reinterpret_cast<uintptr_t>(null_definition), ctor, destroy, sink) == ERROR_NOT_FOUND && !calls,
          "null original definition is not replaced with an invented key");
    alignas(8) unsigned char decree[24]{};
    for (uint32_t value : {uint32_t(0), uint32_t(UINT32_MAX), uint32_t(0xAB001234)}) {
        struct IdOutput { uint32_t value, canary; } result{0, 0xDEADBEEF};
        std::memcpy(decree + 0x10, &value, 4); std::memset(decree + 0x14, 0xCD, 4);
        check(native::decree_id(reinterpret_cast<uintptr_t>(decree), &result.value) == ERROR_SUCCESS
            && result.value == value && result.canary == 0xDEADBEEF && decree[0x14] == 0xCD,
            "native Decree ID copies exact full32 zero/UINTMAX/generation bits without mask or adjacent write");
    }
    uint32_t absent_id = 99;
    check(native::decree_id(0, &absent_id) == ERROR_NOT_FOUND && absent_id == 0,
          "native Decree ID null source reports missing instead of inventing a usable ID");
    PdxV3DataPageField invalid_id{PDX_V3_DATA_FIELD_DECREE_ID, 128, 3, 0};
    check(!native::valid_fields(&invalid_id, 1, [](PdxV3DataPageSpan) { return true; }),
          "native Decree ID accepts only original current-row scope not a guessed receiver expression");
    // Actual production entry preflight only. Every packet below is malformed
    // and must return before touching a game RVA in this owned process.
    for (unsigned mode = 0; mode < 5; ++mode) {
        const size_t header = sizeof(PdxV3DataPageRequest) + sizeof(PdxV3DataPageScope);
        std::vector<unsigned char> bytes(header + sizeof(PdxV3DataPageField) + sizeof(PdxV3DataPageSpan) + 8);
        auto* page = reinterpret_cast<PdxV3DataPageRequest*>(bytes.data());
        page->size = static_cast<uint32_t>(bytes.size()); page->flags = PDX_V3_DATA_PAGE_NATIVE_FIELDS;
        page->fields_count = page->rows_count = page->limit = 1;
        page->fields_offset = static_cast<uint32_t>(header);
        page->rows_offset = page->fields_offset + sizeof(PdxV3DataPageField);
        page->source_offset = page->rows_offset + sizeof(PdxV3DataPageSpan); page->source_bytes = 4;
        std::memcpy(bytes.data() + page->source_offset, "Root", 4);
        auto* scope = reinterpret_cast<PdxV3DataPageScope*>(page + 1);
        scope->steps_offset = scope->arguments_offset = static_cast<uint32_t>(header);
        auto* field = reinterpret_cast<PdxV3DataPageField*>(bytes.data() + page->fields_offset);
        field->kind = PDX_V3_DATA_FIELD_DECREE_TYPE_KEY;
        auto* row = reinterpret_cast<PdxV3DataPageSpan*>(bytes.data() + page->rows_offset);
        row->offset = page->source_offset + 8;
        if (mode == 0) field->kind = 99;
        if (mode == 1) field->reserved = 1;
        if (mode == 2) field->expression_offset = 8;
        if (mode == 3) page->source_bytes = 0;
        if (mode == 4) { field->kind = PDX_V3_DATA_FIELD_EXPRESSION;
            field->expression_offset = page->source_offset; field->expression_bytes = 4; }
        PdxV3DataProjectNativeFieldsInterface(nullptr, page);
        check(page->error == ERROR_INVALID_PARAMETER && !page->output_address && !page->cells_count,
              "native field malformed kind/reserved/span/noFreshSource/emptytypedtable rejected before game access");
    }
}

void test_owned_context_input(const char* output_path = nullptr) {
    // Production entry points with null application: no game image/RVA is read.
    std::FILE* file = nullptr;
    if (output_path) {
        const auto opened = fopen_s(&file, output_path, "wb");
        check(!opened && file, "owned-context actual-entry fixture output opens");
    }
    for (unsigned entry = 0; entry < 2; ++entry) {
        for (uint32_t mode = 1; mode <= 5; ++mode) {
            PdxV3DataRequest request{};
            request.size = sizeof(request); request.action = PDX_V3_DATA_CREATE_OWNED_CONTEXT;
            request.flags = mode; request.type_id = -1; request.value_handle = 901;
            if (entry) PdxV3DataCreateOwnedContextInterface(nullptr, &request);
            else PdxV3DataInterface(nullptr, &request);
            std::printf("EARLY_RETURN entry=%u mode=%u error=%lu handle=%llu\n", entry, mode,
                request.error, static_cast<unsigned long long>(request.value_handle));
            check(request.error == ERROR_NOT_READY && !request.value_handle
                && request.action == PDX_V3_DATA_CREATE_OWNED_CONTEXT && request.flags == mode,
                "owned-context appnull failure cannot echo source as published owner");
            if (file) check(std::fwrite(&request, sizeof(request), 1, file) == 1,
                            "owned-context actual-entry fixture record written");
        }
    }
    if (file) check(std::fclose(file) == 0, "owned-context actual-entry fixture output closes");
    for (unsigned entry = 0; entry < 2; ++entry) {
        PdxV3DataRequest request{};
        request.size = offsetof(PdxV3DataRequest, value_handle) + sizeof(request.value_handle);
        request.action = PDX_V3_DATA_CREATE_OWNED_CONTEXT; request.flags = PDX_V3_DATA_SHIP_NAME_CONTEXT;
        request.error = 0xAA55; request.value_handle = 901;
        DWORD status = ERROR_INVALID_PARAMETER;
        if (entry) status = PdxV3DataOwnedContext(&request);
        else PdxV3DataInterface(nullptr, &request);
        check(status == ERROR_INVALID_PARAMETER && request.error == 0xAA55 && !request.value_handle,
              "owned-context readable input is cleared before incomplete-header validation return");
        request.size = offsetof(PdxV3DataRequest, value_handle); request.value_handle = 901;
        if (entry) status = PdxV3DataOwnedContext(&request);
        else PdxV3DataInterface(nullptr, &request);
        check(status == ERROR_INVALID_PARAMETER && request.value_handle == 901,
              "owned-context validation never writes a handle outside declared readable prefix");
    }
    PdxV3DataRequest snapshot{};
    snapshot.size = sizeof(snapshot); snapshot.action = PDX_V3_DATA_SNAPSHOT; snapshot.value_handle = 901;
    PdxV3DataInterface(nullptr, &snapshot);
    check(snapshot.error == ERROR_NOT_READY && snapshot.value_handle == 901,
          "unrelated snapshot input handle semantics are unchanged on appnull failure");
}

void test_game_rule_selection() {
    namespace policy = pdx::v3_game_rule_selection;
    check(sizeof(PdxV3DataGameRuleRequest) == 160, "game-rule copied request additive ABI160");
    check(policy::plan(0, UINT32_MAX, UINT32_MAX).outcome == PDX_V3_DATA_RULE_EMPTY,
          "game-rule actual zero canonical choices distinct from missing row");
    check(policy::plan(4, 1, UINT32_MAX).outcome == PDX_V3_DATA_RULE_TARGET_ABSENT,
          "game-rule absent canonical target invokes no action");
    check(policy::plan(4, UINT32_MAX, 2).outcome == PDX_V3_DATA_RULE_CURRENT_NOT_MEMBER,
          "game-rule foreign current selection does not use native fallback index");
    const auto same = policy::plan(4, 2, 2);
    check(same.outcome == PDX_V3_DATA_RULE_ALREADY_SELECTED && !same.steps && !same.direction,
          "game-rule actual identical pointer position is zero-action observation");
    const auto next = policy::plan(4, 3, 0), prev = policy::plan(4, 0, 3), tie = policy::plan(4, 0, 2);
    check(next.direction == 1 && next.steps == 1, "game-rule original Next wraps canonical ordering");
    check(prev.direction == 2 && prev.steps == 1, "game-rule original Prev wraps canonical ordering");
    check(tie.direction == 1 && tie.steps == 2, "game-rule shortest path tie uses original Next");
    const auto large = policy::plan(100, 0, 49);
    check(large.steps == 49 && large.direction == 1, "game-rule n-1 bound has no unrelated sequence32 cap");
    const auto wide = policy::plan(UINT32_MAX, 0, UINT32_MAX - 1);
    check(wide.steps == 1 && wide.direction == 2, "game-rule path math cannot wrap uint32 operands");
    struct BorrowedAdapter {
        // Owned fixture only: injected originals modify the SAME borrowed row.
        struct Row { uintptr_t definition = 1; uintptr_t selected = 10; } row;
        std::vector<PdxV3DataActionRequest> receipts = std::vector<PdxV3DataActionRequest>(100);
        uint32_t calls = 0, returned = 0; bool stopped = false; int reject = -1, fault = -1;
        void invoke(size_t index) {
            auto& r = receipts[index]; ++calls; r.invoked = 1;
            if (static_cast<int>(index) == reject) return;
            ++row.selected; r.accepted = 1;
            if (static_cast<int>(index) == fault) throw std::runtime_error("original rule fault");
        }
        void completed(size_t count) { returned = static_cast<uint32_t>(count); }
        bool continue_after(size_t index) { return PdxV3DataContinueAction(receipts[index], 1); }
        void stop() { stopped = true; }
    } accepted;
    policy::execute(large, 100, accepted);
    check(accepted.calls == 49 && accepted.returned == 49 && accepted.row.selected == 59 && !accepted.stopped,
          "production rule orchestration keeps one borrowed row across bounded original actions");
    BorrowedAdapter rejected; rejected.reject = 1;
    policy::execute(large, 100, rejected); // A real suffix remains after rejected step1.
    check(rejected.calls == 2 && rejected.returned == 2 && rejected.stopped && rejected.row.selected == 11
          && rejected.receipts[0].accepted == 1 && rejected.receipts[1].invoked == 1 && !rejected.receipts[1].accepted,
          "production rule rejected suffix keeps original prefix mutation and exact action flags");
    BorrowedAdapter partial; partial.receipts[0].result.error = ERROR_MORE_DATA;
    policy::execute(tie, 100, partial);
    check(partial.calls == 2 && partial.returned == 2 && partial.receipts[0].result.error == ERROR_MORE_DATA,
          "production rule accepted postcopy234 preserves error and continues by original flags");
    BorrowedAdapter fault; fault.fault = 1; bool caught = false;
    try { policy::execute(tie, 100, fault); } catch (const std::runtime_error&) { caught = true; }
    check(caught && fault.calls == 2 && fault.returned == 1 && fault.row.selected == 12 && fault.receipts[1].accepted == 1,
          "production rule fault preserves current observed receipt beyond completed without rollback");
    BorrowedAdapter capacity;
    policy::execute(large, 3, capacity);
    check(capacity.calls == 3 && capacity.returned == 3 && capacity.row.selected == 13,
          "production rule output capacity never invokes an unrecordable suffix");
    PdxV3DataActionRequest copied_failure{};
    copied_failure.result.kind = PDX_V3_DATA_RAW; copied_failure.result.error = ERROR_INVALID_DATA;
    copied_failure.result.factory = copied_failure.result.object = copied_failure.result.value_handle = 0x9876;
    copied_failure.invoked = copied_failure.accepted = 1;
    { PdxV3DataCopiedBoundary boundary{&copied_failure.result}; }
    check(!copied_failure.result.factory && !copied_failure.result.object && !copied_failure.result.value_handle
          && copied_failure.result.error == ERROR_INVALID_DATA && copied_failure.invoked == 1 && copied_failure.accepted == 1,
          "production copied boundary scrubs pointer outputs on rule failure without changing original error or flags");
    bool unwound = false;
    try {
        PdxV3DataCopiedBoundary boundary{&copied_failure.result};
        copied_failure.result.factory = copied_failure.result.object = 0x8765;
        throw std::runtime_error("original rule exception");
    } catch (const std::runtime_error&) { unwound = true; }
    check(unwound && !copied_failure.result.factory && !copied_failure.result.object
          && copied_failure.result.error == ERROR_INVALID_DATA && copied_failure.invoked == 1 && copied_failure.accepted == 1,
          "production copied boundary scrubs rule exception outputs and retains original receipt");
    check(PdxV3DataSelectGameRule(nullptr) == ERROR_INVALID_PARAMETER,
          "production rule null boundary reaches no native game function");
    for (uint32_t mode = 0; mode < 6; ++mode) {
        std::vector<unsigned char> buffer(sizeof(PdxV3DataGameRuleRequest) + 2 * sizeof(PdxV3DataInput));
        auto* r = reinterpret_cast<PdxV3DataGameRuleRequest*>(buffer.data());
        r->size = static_cast<uint32_t>(buffer.size());
        r->rule_input_offset = sizeof(*r); r->rule_input_bytes = sizeof(PdxV3DataInput);
        r->setting_input_offset = r->rule_input_offset + r->rule_input_bytes; r->setting_input_bytes = sizeof(PdxV3DataInput);
        r->output_offset = r->size;
        reinterpret_cast<PdxV3DataInput*>(buffer.data() + r->rule_input_offset)->kind = PDX_V3_DATA_STRING;
        reinterpret_cast<PdxV3DataInput*>(buffer.data() + r->setting_input_offset)->kind = PDX_V3_DATA_STRING;
        if (mode == 0) r->rule_input_offset = 0;
        if (mode == 1) r->setting_input_offset = r->rule_input_offset;
        if (mode == 2) r->output_offset = r->setting_input_offset;
        if (mode == 3) r->setting_input_bytes += 8;
        if (mode == 4) reinterpret_cast<PdxV3DataInput*>(buffer.data() + r->rule_input_offset)->kind = PDX_V3_DATA_UTF8_INPUT;
        if (mode == 5) reinterpret_cast<PdxV3DataInput*>(buffer.data() + r->setting_input_offset)->string_bytes = 1;
        check(PdxV3DataSelectGameRule(r) == ERROR_INVALID_PARAMETER && !r->completed && !r->receipts_count && !r->row_observed,
              "production rule typed/control/allocation spans reject before factory or native borrow");
    }
}

void test_source_index_input() {
    PdxV3DataInput input{PDX_V3_DATA_BOOL, 9, 99, 88};
    check(PdxV3DataSourceIndexInput(0, 0, &input) == ERROR_NOT_FOUND && input.raw_bits == 99,
          "empty fresh source never produces an action index");
    check(PdxV3DataSourceIndexInput(3, 3, &input) == ERROR_NOT_FOUND && input.raw_bits == 99,
          "one-past source ordinal stops before provider or action");
    check(PdxV3DataSourceIndexInput(UINT32_MAX, 3, &input) == ERROR_NOT_FOUND,
          "out-of-range native ordinal cannot enter root action");
    check(PdxV3DataSourceIndexInput(0, 3, &input) == ERROR_SUCCESS && input.kind == PDX_V3_DATA_I32
          && input.raw_bits == 0 && !input.value_handle && !input.string_bytes,
          "valid zero ordinal constructs genuine I32 instead of trusting independent input");
    check(PdxV3DataSourceIndexInput(2, 3, &input) == ERROR_SUCCESS && input.raw_bits == 2,
          "last current source ordinal becomes exact original GUI I32 input");
    // Exercise the production Element dispatcher before any factory/game read.
    // A mistaken pointer-array classification would overwrite expression bytes
    // at ElementHeader+32/+36 with array count/stages, even on early rejection.
    std::vector<unsigned char> ordinary(sizeof(PdxV3DataElementRequest) + 80 + sizeof(PdxV3DataInput));
    auto* selected = reinterpret_cast<PdxV3DataElementRequest*>(ordinary.data());
    auto& packet = selected->action.result;
    packet.size = static_cast<uint32_t>(ordinary.size()); packet.action = PDX_V3_DATA_INVOKE_SOURCE_INDEX_ACTION;
    packet.flags = PDX_V3_DATA_EXPLICIT_ACTION; packet.start = UINT32_MAX;
    packet.text_offset = sizeof(*selected); packet.text_bytes = 64;
    packet.bindings_offset = packet.output_offset = packet.size;
    selected->source_text_offset = sizeof(*selected) + 64; selected->source_text_bytes = 16;
    selected->action.input_offset = sizeof(*selected) + 80; selected->action.input_bytes = sizeof(PdxV3DataInput);
    std::memset(ordinary.data() + sizeof(*selected), 'A', 80);
    check(PdxV3DataElement(selected) == ERROR_INVALID_PARAMETER && !selected->action.invoked,
          "production source-index validation rejects signed overflow before factory/game access");
    bool preserved = true;
    for (size_t i = sizeof(*selected); i < sizeof(*selected) + 80; ++i) preserved = preserved && ordinary[i] == 'A';
    check(preserved, "source-index ordinary Element wire is never reinterpreted/written as pointer-array suffix");
    PdxV3DataElementRequest request{};
    request.action.result.size = sizeof(request); request.action.result.action = PDX_V3_DATA_INVOKE_SOURCE_INDEX_ACTION;
    PdxV3DataInvokeSourceIndexInterface(nullptr, &request);
    check(request.action.result.error == ERROR_NOT_READY && !request.action.invoked,
          "source index capability alias retains original Interface app boundary without game access");
}

std::vector<unsigned char> packet() {
    std::vector<unsigned char> bytes(sizeof(PdxV3DataProjectionRequest) + 16);
    auto* p = reinterpret_cast<PdxV3DataProjectionRequest*>(bytes.data());
    p->result.size = static_cast<uint32_t>(bytes.size());
    p->result.action = PDX_V3_DATA_SOURCE_COUNT;
    p->result.text_offset = sizeof(*p); p->result.text_bytes = 1;
    p->result.bindings_offset = p->result.output_offset = sizeof(*p) + 8;
    p->source_world = 0; // Must reject BEFORE native factory/profile/guard access.
    p->result.factory = p->result.object = p->result.value_handle = 0x1234;
    p->result.any_type = p->result.any_vtable = p->result.payload_address = 0x4321;
    std::memset(p->result.raw_any, 0xA5, sizeof(p->result.raw_any));
    p->source_produced = p->count_valid = p->source_count = 77;
    return bytes;
}
}
int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "rule-fields") == 0) { test_rule_fields(); return failures ? 1 : 0; }
    if (argc == 3 && std::strcmp(argv[1], "rule-fields-wire") == 0) { test_rule_fields_wire(argv[2]); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "company-source-count") == 0) { test_company_source_count_observation(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "page-publication") == 0) { test_page_publication_errors(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "company-sources") == 0) { test_company_page_sources(); return failures ? 1 : 0; }
    if (argc == 3 && std::strcmp(argv[1], "company-sources-wire") == 0) { test_company_sources_wire(argv[2]); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "original-sources") == 0) { test_original_page_sources(); return failures ? 1 : 0; }
    if (argc == 3 && std::strcmp(argv[1], "original-sources-wire") == 0) { test_original_sources_wire(argv[2]); return failures ? 1 : 0; }
    if (argc == 3 && std::strcmp(argv[1], "native-fields-wire") == 0) { test_native_fields_wire(argv[2]); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "native-fields") == 0) { test_native_fields(); return failures ? 1 : 0; }
    if ((argc == 2 || argc == 3) && std::strcmp(argv[1], "owned-context-input") == 0) { test_owned_context_input(argc == 3 ? argv[2] : nullptr); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "action-sequence") == 0) { test_action_sequence(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "source-index") == 0) { test_source_index_input(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "game-rule") == 0) { test_game_rule_selection(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "prefix-arguments") == 0) { test_prefix_arguments(); return failures ? 1 : 0; }
    if (argc == 2 && std::strcmp(argv[1], "grouped-page") == 0) { test_grouped_page(); return failures ? 1 : 0; }
    std::puts("OWNED HOST: projection ABI/preconditions only; REAL GAME NOT RUN");
    test_owned_context_input();
    test_native_fields();
    test_original_page_sources();
    test_source_index_input();
    test_action_sequence();
    test_game_rule_selection();
    test_prefix_arguments();
    test_grouped_page();
    check(sizeof(PdxV3DataProjectionRequest) == 392, "projection additive ABI392");
    check(PdxV3DataProjection(nullptr) == ERROR_INVALID_PARAMETER, "null rejected");
    for (unsigned mode = 0; mode < 6; ++mode) {
        auto bytes = packet();
        auto* p = reinterpret_cast<PdxV3DataProjectionRequest*>(bytes.data());
        if (mode) p->source_world = 1;
        if (mode == 1) p->result.action = PDX_V3_DATA_INVOKE_ACTION;
        if (mode == 2) p->result.flags = PDX_V3_DATA_EXPLICIT_ACTION;
        if (mode == 3) p->reserved = 1;
        if (mode == 4) p->result.text_offset = 0;
        if (mode == 5) p->result.output_offset = static_cast<uint32_t>(bytes.size() + 1);
        check(PdxV3DataProjection(p) == ERROR_INVALID_PARAMETER, "invalid request rejected before game access");
        check(!p->source_produced && !p->count_valid && !p->source_count,
              "no fabricated source/count evidence on failure");
        bool scrubbed = !p->result.factory && !p->result.object && !p->result.value_handle
            && !p->result.any_type && !p->result.any_vtable && !p->result.payload_address;
        for (auto b : p->result.raw_any) scrubbed = scrubbed && !b;
        check(scrubbed, "copied-only boundary scrubs all native pointer outputs");
    }
    // Production helper, owned storage only: no game VM is faked or entered.
    static_assert(!std::is_copy_constructible<PdxV3DataScopedValue>::value);
    for (DWORD completion : {DWORD(ERROR_SUCCESS), DWORD(ERROR_MORE_DATA), DWORD(ERROR_INVALID_DATA)}) {
        uint64_t held = 0;
        const int before = releases;
        {
            PdxV3DataScopedValue owner{&held, release_owned};
            held = 9; // A newly produced owned test value, never a game pointer.
            (void)completion; // Scope cleanup is identical for each completed status.
        }
        check(held == 0 && releases == before + 1, "same-scope owner cleanup on success/partial/error");
    }
    uint64_t held = 0;
    const int before = releases;
    try {
        PdxV3DataScopedValue owner{&held, release_owned}; held = 11;
        throw 4;
    } catch (int) {}
    check(!held && releases == before + 1, "same-scope owner cleanup during C++ unwinding");
    struct OwnedComplete { uintptr_t vtable; unsigned char bytes[0x98]; } object{0xABC, {}};
    uintptr_t selected = 0;
    const auto address = reinterpret_cast<uintptr_t>(&object);
    check(PdxV3DataOriginalSubobject(address, 0x88, object.vtable, &selected) == ERROR_SUCCESS
        && selected == address + 0x88, "original embedded subobject ADDRESS selected, not pointer-field content");
    check(PdxV3DataOriginalSubobject(address, 0x88, 0xBAD, &selected) == ERROR_INVALID_DATA
        && selected == 0, "wrong original vtable cannot be retagged");
    check(PdxV3DataOriginalSubobject(UINTPTR_MAX - 7, 0x88, 0xABC, &selected) == ERROR_ARITHMETIC_OVERFLOW
        && selected == 0, "overflow rejected before any object dereference");
    check(PdxV3DataOriginalSubobject(address, 7, 0xABC, &selected) == ERROR_INVALID_PARAMETER,
        "unaligned embedded offset rejected");
    namespace core = pdx::data_projection_core;
    {
        releases = 0; uint64_t handle = 0, published = 0;
        {
            PdxV3DataScopedValue owner{&handle, release_owned};
            const auto status = core::capture_produced(handle,
                [&] { published = 0x1234; return ERROR_MORE_DATA; },
                [&]() noexcept { return published; });
            check(status == ERROR_MORE_DATA && handle == 0x1234 && releases == 0,
                  "shared publication capture transfers produced handle before enclosing release");
        }
        check(releases == 1 && handle == 0, "captured partial producer releases exactly once");
        releases = 0; handle = published = 0; bool caught_late = false;
        try {
            PdxV3DataScopedValue owner{&handle, release_owned};
            core::capture_produced(handle,
                [&]() -> DWORD { published = 0x5678; throw std::runtime_error("late snapshot failure"); },
                [&]() noexcept { return published; });
        } catch (const std::runtime_error&) { caught_late = true; }
        check(caught_late && releases == 1 && handle == 0,
              "late post-publication exception captures and releases original producer without replay");
    }
    uintptr_t owned_elements[] = {address, 0};
    core::PointerArrayDescriptor descriptor{reinterpret_cast<uintptr_t>(owned_elements), 2, 2, sizeof(uintptr_t), 4};
    uintptr_t element = 9;
    auto read_owned = [](uintptr_t at, uintptr_t& out) { out = *reinterpret_cast<const uintptr_t*>(at); return true; };
    check(core::pointer_element(descriptor, 0, read_owned, &element) == core::Error::ok && element == address,
          "shared traversal uses injected descriptor/read over owned storage");
    check(core::pointer_element(descriptor, 1, read_owned, &element) == core::Error::ok && element == 0,
          "shared traversal preserves zero element; adapter decides its meaning");
    core::CountObservation count;
    check(core::observe_source_count([] { return true; }, [](uint64_t& out) { out = 0; return true; }, count)
        && count.source_produced && count.count_valid && count.count == 0, "shared produced empty differs from missing source");
    check(!core::observe_source_count([] { return false; }, [](uint64_t&) { return false; }, count)
        && !count.source_produced && !count.count_valid, "shared failure never fabricates an empty collection");
    const bool native_false = false; bool copied_false = true;
    auto copied = core::copy_bytes(&native_false, sizeof(native_false), &copied_false, sizeof(copied_false));
    check(!copied_false && !copied.truncated, "shared byte copy preserves owned False");
    copied = core::copy_bytes(&native_false, sizeof(native_false), nullptr, 0);
    check(copied.copied == 0 && copied.required == sizeof(native_false) && copied.truncated,
          "shared byte budget reports required size without retaining a value");
    std::vector<unsigned char> map_bytes(sizeof(PdxV3UiMapRequest) + 2 * sizeof(PdxV3UiMapRecord) + 16);
    auto* map = reinterpret_cast<PdxV3UiMapRequest*>(map_bytes.data());
    *map = {}; map->size = static_cast<uint32_t>(map_bytes.size()); map->action = PDX_V3_UI_MAP_SNAPSHOT;
    map->source_world = 1; map->interface_thread_id = GetCurrentThreadId(); map->max_items = 2;
    map->records_offset = sizeof(*map); map->text_offset = sizeof(*map) + 2 * sizeof(PdxV3UiMapRecord); map->text_capacity = 16;
    check(sizeof(*map) == 80 && PdxV3UiMapValidate(map) == ERROR_SUCCESS, "map copied request ABI/bounds validate without game");
    PdxV3UiMapNotificationsInterface(nullptr, map);
    check(map->error == ERROR_NOT_SUPPORTED && !map->source_produced && !map->collection_valid,
          "owned non-game executable reports unsupported, not successful empty map source");
    map->source_world = 0; map->source_produced = map->collection_valid = 9;
    check(PdxV3UiMapValidate(map) == ERROR_INVALID_PARAMETER && !map->source_produced && !map->collection_valid,
          "map invalid source request clears production evidence before native access");
    check(sizeof(PdxV3DataScopedProjectionRequest) == 408 && sizeof(PdxV3DataCreationStep) == 40,
          "nested additive ABI408 plus typed step40");
    for (unsigned mode = 0; mode < 7; ++mode) {
        constexpr auto fixed = sizeof(PdxV3DataScopedProjectionRequest) + sizeof(PdxV3DataCreationStep);
        std::vector<unsigned char> storage(fixed + 8 + 8);
        auto* nested = reinterpret_cast<PdxV3DataScopedProjectionRequest*>(storage.data());
        auto* request = &nested->projection.result;
        request->size = static_cast<uint32_t>(storage.size()); request->action = PDX_V3_DATA_EVALUATE_SCOPED;
        nested->projection.source_world = mode ? 1 : 0;
        nested->steps_offset = sizeof(*nested); nested->steps_count = 1;
        auto* step = reinterpret_cast<PdxV3DataCreationStep*>(storage.data() + nested->steps_offset);
        step->source_text_offset = static_cast<uint32_t>(fixed); step->source_text_bytes = 4;
        step->expected_count = UINT32_MAX;
        step->source_produced = step->count_valid = step->source_count = step->element_obtained = 77;
        step->element_type_id = 77; step->binding_mode = 77;
        std::memcpy(storage.data() + fixed, "RootName", 8);
        request->text_offset = static_cast<uint32_t>(fixed + 4); request->text_bytes = 4;
        request->bindings_offset = request->output_offset = static_cast<uint32_t>(fixed + 8); request->output_bytes = 8;
        request->factory = request->object = request->value_handle = request->payload_address = 0xBAD;
        request->any_type = request->any_vtable = 0xBAD;
        std::memset(request->raw_any, 0xA5, sizeof(request->raw_any));
        if (mode == 1) request->flags = 1;
        if (mode == 2) nested->reserved = 1;
        if (mode == 3) storage[fixed] = 0xC0;
        if (mode == 4) step->expected_count = 0x80000000;
        if (mode == 5) step->index = 0x80000000;
        if (mode == 6) request->output_offset = 0;
        check(PdxV3DataProjection(&nested->projection) == ERROR_INVALID_PARAMETER,
              "nested semantic/range/UTF8/world failure rejected before game access");
        check(!nested->projection.source_produced && !nested->projection.count_valid
            && !nested->projection.source_count && !nested->completed_steps
            && !step->source_produced && !step->count_valid && !step->source_count
            && !step->element_obtained && step->element_type_id == -1 && step->binding_mode == UINT32_MAX,
              "nested invalid packet cannot keep caller-preseeded source/count/binding receipts");
        bool clean = !request->factory && !request->object && !request->value_handle && !request->payload_address
            && !request->any_type && !request->any_vtable;
        for (auto byte : request->raw_any) clean = clean && byte == 0;
        check(clean, "nested failure scrubs every native pointer/Any boundary field");
    }
    {
        const uint32_t items = 1, fields = items * PDX_V3_UI_DIALOG_MAX_FIELDS;
        const auto fixed = sizeof(PdxV3UiDialogsRequest) + items * sizeof(PdxV3UiDialogRecord) + fields * sizeof(PdxV3UiDialogField);
        for (uint32_t family = PDX_V3_UI_JOMINI_NOTIFICATIONS; family <= PDX_V3_UI_CONFIRMATIONS; ++family) {
            std::vector<unsigned char> storage(fixed + 32);
            auto* dialogs = reinterpret_cast<PdxV3UiDialogsRequest*>(storage.data());
            dialogs->size = static_cast<uint32_t>(storage.size()); dialogs->action = PDX_V3_UI_DIALOGS_SNAPSHOT;
            dialogs->source = family; dialogs->source_world = 1; dialogs->interface_thread_id = GetCurrentThreadId();
            dialogs->max_items = items; dialogs->records_offset = sizeof(*dialogs);
            dialogs->fields_offset = sizeof(*dialogs) + items * sizeof(PdxV3UiDialogRecord);
            dialogs->fields_capacity = fields; dialogs->text_offset = static_cast<uint32_t>(fixed); dialogs->text_capacity = 32;
            check(sizeof(*dialogs) == 96 && sizeof(PdxV3UiDialogRecord) == 32 && sizeof(PdxV3UiDialogField) == 40,
                  "dialog copied wire ABI96/record32/field40");
            check(PdxV3UiDialogsValidate(dialogs) == ERROR_SUCCESS, "all three original-source families validate owned wire only");
            PdxV3UiDialogsInterface(nullptr, dialogs);
            check(dialogs->error == ERROR_NOT_SUPPORTED && !dialogs->source_produced && !dialogs->count_valid && !dialogs->returned_count,
                  "owned non-game dialog source rejects profile before layout/VM calls, not successful empty");
            dialogs->source_world = 0; dialogs->source_produced = dialogs->count_valid = dialogs->source_count = 99;
            check(PdxV3UiDialogsValidate(dialogs) == ERROR_INVALID_PARAMETER && !dialogs->source_produced && !dialogs->count_valid && !dialogs->source_count,
                  "invalid dialog preflight clears caller-preseeded production/count receipts");
        }
    }
    {
        SYSTEM_INFO system{}; GetSystemInfo(&system); const auto page = system.dwPageSize;
        auto* memory = static_cast<unsigned char*>(VirtualAlloc(nullptr, page * 2, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
        check(memory != nullptr, "shared-memory test owns its page allocation");
        if (memory) {
            std::memset(memory, 0x2A, page * 2); unsigned char bytes[8]{}; uint32_t received = 0; DWORD previous = 0;
            check(PdxRuntimeRead(reinterpret_cast<uint64_t>(memory), bytes, sizeof(bytes), &received) == ERROR_SUCCESS
                && received == sizeof(bytes) && bytes[0] == 0x2A, "Map and dialogs reuse shared exact readable-page primitive");
            const auto protected_page = VirtualProtect(memory + page, page, PAGE_NOACCESS, &previous);
            check(protected_page != 0, "owned second page is protected for partial-read regression");
            received = 99;
            check(PdxRuntimeRead(reinterpret_cast<uint64_t>(memory + page - 1), bytes, 2, &received) != ERROR_SUCCESS && received < 2,
                  "shared read cannot claim exact success across unreadable page boundary");
            DWORD ignored = 0; VirtualProtect(memory + page, page, PAGE_READWRITE | PAGE_GUARD, &ignored);
            received = 99;
            check(PdxRuntimeRead(reinterpret_cast<uint64_t>(memory + page), bytes, 1, &received) != ERROR_SUCCESS && received == 0,
                  "shared read rejects owned guard page without fabricated bytes");
            received = 99; std::memset(bytes, 0xA5, sizeof(bytes));
            constexpr DWORD last_error_canary = 0xE0010042;
            SetLastError(last_error_canary);
            const auto overflow_error = PdxRuntimeRead(UINT64_MAX, bytes, 2, &received);
            const auto after_error = GetLastError();
            std::printf("READ_OVERFLOW error=%lu received=%u last_error=%lu output=%02X,%02X\n",
                overflow_error, received, after_error, static_cast<unsigned>(bytes[0]), static_cast<unsigned>(bytes[1]));
            check(overflow_error == ERROR_ARITHMETIC_OVERFLOW && received == 0,
                  "shared read rejects overflowing external address range with exact preflight contract");
            check(after_error == last_error_canary && bytes[0] == 0xA5 && bytes[1] == 0xA5,
                  "real DLL overflow preflight leaves last-error and output canaries untouched");
            check(VirtualFree(memory, 0, MEM_RELEASE) != 0, "owned page regression releases exactly its own allocation");
        }
    }
    test_shared_read_preflight();
    test_native_image_identity();
    test_original_source_core();
    test_shared_scope_chain();
    test_shared_page();
    test_reused_constants();
    test_nested_page();
    test_captured_parameter_protocol();
    test_captured_nested_lifetime();
    return failures ? 1 : 0;
}
