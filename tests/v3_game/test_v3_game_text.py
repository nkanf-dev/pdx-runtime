"""Pure V3 display projections. No session, process or native calls."""
import unittest

from pdx_runtime.v3_game._text import display_field, plain_text


class TextViewTests(unittest.TestCase):

    def test_reported_army_markup_and_nested_law_text(self):
        examples = (
            ("\x15tooltippable_name \x15tooltip:dw_army,AAAA,FormationTooltip \x15! 镶黄旗\x15!\x15!", "镶黄旗"),
            ("\x15tooltippable_name \x15tooltip:dw_law,AAAA,LawTooltip 农奴制\x15!\x15!", "农奴制"),
            ("\x15B 土地\x15G 改革\x15!完成\x15!", "土地改革完成"),
            ("#tooltippable_name #tooltip:dw_x,LawTooltip #BOLD 土地#!改革#!#!", "土地改革"),
            ("§G成功§!：§R-25%§!", "成功：-25%"),
        )
        for raw, expected in examples:
            with self.subTest(raw=raw):
                view = display_field(raw)
                self.assertEqual(view, {"name": expected, "name_raw": raw})
                self.assertIs(view["name_raw"], raw)
                self.assertEqual(view["name_raw"].encode("utf-8"), raw.encode("utf-8"))
                self.assertNotIn("dw_", view["name"])

    def test_icons_stop_before_adjacent_unicode_and_preserve_visible_lines(self):
        raw = ("政府对\x16JAP17!\x16flag_overlay!德川幕府采取行动。\n"
               "\x15indent_newline:3 • \x15v; 反国家游说团\x15!支持\x16silk!\xa0丝绸")
        self.assertEqual(plain_text(raw), "政府对德川幕府采取行动。\n\n• 反国家游说团支持\xa0丝绸")
        self.assertEqual(plain_text("@paper!\xa0纸张"), "纸张")

    def test_unicode_and_literal_nonmarkup_text_remain_visible(self):
        for raw in ("镶黄旗", "Île-de-France", "Στρατός", "陆军🟨", "Cafe\u0301",
                    "Iron #1", "#Brewery", "unknown:value", "https://example/#section"):
            with self.subTest(raw=raw):
                self.assertEqual(plain_text(raw), raw)

    def test_unknown_and_malformed_commands_are_not_discarded(self):
        for raw in ("\x15future:payload 可见\x15!", "#future 可见#!", "§z可见§!",
                    "\x16unfinished 图标", "@unfinished 图标", "\x15tooltip: 可见\x15!",
                    "\x15indent_newline:unknown 文本", "\x15future无分隔符", "未知\x17名称", "\x00原文"):
            with self.subTest(raw=raw):
                self.assertEqual(plain_text(raw), raw)
                self.assertEqual(display_field(raw)["name_raw"], raw)

    def test_missing_end_marker_keeps_all_visible_text(self):
        raw = "\x15tooltippable_name \x15tooltip:dw_123,AAAA 标签\n后续文字"
        self.assertEqual(plain_text(raw), "标签\n后续文字")
        self.assertEqual(plain_text("\x15G 标签\x15!后续\x15!"), "标签后续")
        self.assertEqual(plain_text("\x15G木材工具提示\x15!\n预算 #!"), "木材工具提示\n预算")

    def test_only_explicit_display_field_is_projected(self):
        native = {"id": 0, "key": "#BOLD key#!", "name": "#BOLD 名称#!", "error": 1168}
        row = {**native, **display_field(native["name"])}
        self.assertEqual(native, {"id": 0, "key": "#BOLD key#!", "name": "#BOLD 名称#!", "error": 1168})
        self.assertEqual(row["key"], native["key"])
        self.assertEqual(row["error"], 1168)
        self.assertEqual(row["name"], "名称")
        self.assertEqual(row["name_raw"], native["name"])
        self.assertEqual(display_field(native["name"], "formation_name"),
                         {"formation_name": "名称", "formation_name_raw": native["name"]})

    def test_none_zero_false_empty_and_nontext_are_not_coerced(self):
        for raw in (None, 0, False, "", b"\x15G native bytes", object()):
            with self.subTest(raw=raw):
                view = display_field(raw)
                self.assertIs(view["name_raw"], raw)
                self.assertEqual(view["name"], raw)
