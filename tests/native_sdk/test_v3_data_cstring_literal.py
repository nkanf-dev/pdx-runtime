"""Proved plain CString literal byte-codec fixtures, not native/VM acceptance.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import patch

from pdx_runtime import v3_data as data


class PlainCStringLiteralTests(unittest.TestCase):
    def test_exact_empty_and_plain_utf8_expression_bytes(self):
        for value, expected in (("", b"''"), ("plain", b"'plain'"), ("0", b"'0'")):
            with self.subTest(value=value):
                result = data.plain_cstring_literal(value)
                self.assertIs(type(result), str)
                self.assertEqual(result.encode("utf-8", "strict"), expected)

    def test_apostrophe_and_backslash_bytes_use_the_proved_escape_order(self):
        cases = [("O'Brien", "274f5c27427269656e27"),
                 ("A\\B", "27415c5c4227"),
                 ("'", "275c2727"),
                 ("\\", "275c5c27"),
                 ("\\'", "275c5c5c2727"),
                 ("'\\", "275c275c5c27")]
        for value, expected_hex in cases:
            with self.subTest(value=value):
                self.assertEqual(data.plain_cstring_literal(value).encode(), bytes.fromhex(expected_hex))

    def test_injection_shaped_text_remains_inside_one_correctly_quoted_literal(self):
        value = "');Run('x')"
        expected = bytes.fromhex("275c27293b52756e285c27785c272927")
        self.assertEqual(data.plain_cstring_literal(value).encode(), expected)

    def test_actual_cr_lf_tab_and_leading_whitespace_are_not_normalized(self):
        value = " \tA\r\nB\rC\nD\tend "
        self.assertEqual(data.plain_cstring_literal(value).encode(), b"' \tA\r\nB\rC\nD\tend '")

    def test_double_quotes_are_ordinary_unescaped_bytes(self):
        self.assertEqual(data.plain_cstring_literal('a"b').encode(), bytes.fromhex("2761226227"))
        self.assertEqual(data.plain_cstring_literal('"').encode(), b"'\"'")

    def test_backslash_letter_sequences_are_data_not_json_or_unicode_escapes(self):
        value = r"\n\r\t\u1234\x41"
        expected = bytes.fromhex("275c5c6e5c5c725c5c745c5c75313233345c5c78343127")
        self.assertEqual(data.plain_cstring_literal(value).encode(), expected)

    def test_unicode_bytes_are_exact_without_normalization(self):
        self.assertEqual(data.plain_cstring_literal("名é😀").encode(), bytes.fromhex("27e5908dc3a9f09f988027"))
        combining = data.plain_cstring_literal("e\u0301").encode()
        composed = data.plain_cstring_literal("é").encode()
        self.assertEqual(combining, bytes.fromhex("2765cc8127"))
        self.assertEqual(composed, bytes.fromhex("27c3a927"))
        self.assertNotEqual(combining, composed)

    def test_empty_first_converter_name_and_unclosed_parenthesis_are_permitted(self):
        cases = ["()", "()payload", "()x)y", "()((x))", "(", "(x", "((x", "(x(y"]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(data.plain_cstring_literal(value).encode(), b"'" + value.encode() + b"'")

    def test_only_byte_zero_prefix_is_checked_without_stripping(self):
        for value in (" (x)y", "\t(x)y", "\r\n(x)y", "prefix(x)y"):
            with self.subTest(value=value):
                self.assertEqual(data.plain_cstring_literal(value).encode(), b"'" + value.encode() + b"'")

    def test_nonempty_first_converter_prefix_is_rejected_with_original_repr(self):
        cases = ["(x)", "(x)y", "( )payload", "(\t)payload", "(\n)payload", "(é)", "((x))", "(\\)payload"]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                data.plain_cstring_literal(value)
            self.assertIn(repr(value), str(caught.exception))

    def test_non_string_inputs_are_value_error_and_identify_original_request(self):
        marker = object()
        for value in (None, False, True, 0, 1.25, b"plain", bytearray(b"x"), ["x"], marker):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                data.plain_cstring_literal(value)
            self.assertIn(repr(value), str(caught.exception))

    def test_nul_is_rejected_not_truncated_and_original_request_is_identified(self):
        for value in ("\0", "A\0B", "\0prefix", "suffix\0", "(x)A\0B"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                data.plain_cstring_literal(value)
            self.assertIn(repr(value), str(caught.exception))

    def test_strict_utf8_failure_keeps_original_value_object(self):
        for value in ("\ud800", "before\udfffafter", "\ud800\udfff"):
            with self.subTest(value=repr(value)), self.assertRaises(UnicodeEncodeError) as caught:
                data.plain_cstring_literal(value)
            self.assertIs(caught.exception.object, value)
            self.assertEqual(caught.exception.encoding, "utf-8")

    def test_original_unicode_encode_error_identity_and_details_propagate(self):
        class FailingString(str):
            def encode(self, encoding="utf-8", errors="strict"):
                self.seen = (encoding, errors)
                raise failure
        value = FailingString("original")
        failure = UnicodeEncodeError("utf-8", value, 1, 3, "original encode failure")
        with self.assertRaises(UnicodeEncodeError) as caught:
            data.plain_cstring_literal(value)
        self.assertIs(caught.exception, failure)
        self.assertIs(caught.exception.object, value)
        self.assertEqual((caught.exception.start, caught.exception.end, caught.exception.reason),
                         (1, 3, "original encode failure"))
        self.assertEqual(value.seen, ("utf-8", "strict"))

    def test_helper_never_submits_callback_or_builds_page_or_opens_bridge(self):
        with patch.object(data, "submit_callback") as submit, patch.object(data, "_page_request") as page, \
                patch.object(data, "NativeBridge") as bridge:
            self.assertEqual(data.plain_cstring_literal("example"), "'example'")
        submit.assert_not_called()
        page.assert_not_called()
        bridge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
