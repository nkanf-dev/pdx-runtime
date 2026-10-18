"""Explicit exact-build definitions for V3 adapters.

These stored positions describe the original V3 ArticleType provider and layout.
Importing the catalog selects no process and performs no probing. Consumers pass
this catalog explicitly to the shared verifier; the verifier imports no game.
"""
from __future__ import annotations

from .target_descriptor import FunctionLocation, TargetDefinition

# Original stored PE fingerprint / provider entry; no probing on import.
VICTORIA3_1_13_11 = TargetDefinition(
    game='victoria3', version='1.13.11',
    sha256='7f53e3260cf46b6247f6906837464944038c4f74e860274ad3d394e0c04f811f',
    preferred_image_base=0x140000000, pe_offset=0x170, timestamp=0x6A846779,
    image_size=0x5DC6000, section_count=6,
    functions={
        'article_type_provider': FunctionLocation(0x7B6A80, bytes.fromhex('48895c240848896c2410488974241857')),
    },
    globals={},
    layout={
        'database_header_size': 0x90, 'database_array': 0x80,
        'database_capacity': 0x88, 'database_count': 0x8C,
        'definition_prefix_size': 0x48, 'definition_ordinal': 0x10,
        'definition_kind': 0x40, 'definition_payer': 0x41,
        'definition_key': 0x18, 'key_length': 0x28, 'key_capacity': 0x30,
        'key_inline_limit': 15,
    },
)
KNOWN_TARGETS = (VICTORIA3_1_13_11,)
