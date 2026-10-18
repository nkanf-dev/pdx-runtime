#pragma once
#include "v3_data_native_fields.h"

// Exact V3 DIRECT receiver facts. The original Scope flag is NOT a generic
// Any-payload discriminator: const raw mode0 carries flag1 and a real pointer.
namespace pdx::v3_rule_fields {
inline constexpr const char* gui_type_name = "GuiGameRule";
inline constexpr const char* setting_type_name = "GameRuleSetting";
inline DWORD definition(const PdxV3DataScope& scope, uint32_t binding_mode,
    int32_t gui_type, int32_t setting_type, uintptr_t* rule) {
    if (!rule) return ERROR_INVALID_PARAMETER;
    *rule = 0;
    if (gui_type >= 0 && scope.type_id == gui_type && binding_mode == 1 && scope.is_any == 0) {
        // Mutable original16B {Rule*, currentSetting*} row, not a CPdx wrapper.
        if (!scope.object) return ERROR_NOT_FOUND;
        *rule = v3_native_fields::pointer_at(scope.object);
    } else if (setting_type >= 0 && scope.type_id == setting_type && binding_mode == 0 && scope.is_any == 1) {
        // Original const Setting POINTER VALUE. Flag1 is preserved, not decoded
        // as an Any and not rejected merely because its old name is is_any.
        if (!scope.object) return ERROR_NOT_FOUND;
        *rule = v3_native_fields::pointer_at(scope.object + 0x40);
    } else return ERROR_NOT_SUPPORTED;
    return *rule ? ERROR_SUCCESS : ERROR_NOT_FOUND;
}
template<class CopyCtor, class Destroy, class Consume>
DWORD copy_key(const PdxV3DataScope& scope, uint32_t binding_mode,
    int32_t gui_type, int32_t setting_type, CopyCtor copy, Destroy destroy, Consume consume) {
    uintptr_t rule = 0;
    const auto status = definition(scope, binding_mode, gui_type, setting_type, &rule);
    if (status) return status;
    // Canonical Rule definition CString+18; never Decree wrapper+10/GetName,
    // a guessed GameRule.GetKey, a choice key, or a default resolver.
    return v3_native_fields::copy_cstring_at(rule + 0x18, copy, destroy, consume);
}
}
