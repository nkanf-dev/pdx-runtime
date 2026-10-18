"""Explicit legacy module selectors for the one-DLL distribution.

The raw process resolver does not import or select this policy. A caller may
pass this map to resolve_export(aliases=...) when it chooses this distribution.
Actual separately loaded modules and explicit integer bases still take priority.
"""
from types import MappingProxyType

from .native_bridge import RUNTIME_DLL

RUNTIME_EXPORT_ALIASES = MappingProxyType({
    "pdx_native_call.dll": RUNTIME_DLL,
    "pdx_runtime_bridge.dll": RUNTIME_DLL,
})
