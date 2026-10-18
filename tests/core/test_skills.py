"""Player-facing skill contracts; no process connection or native call is made."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("skill_validator", ROOT / "tools" / "validate_agent_skills.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class SkillDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = validator.Catalog(ROOT / "src" / "pdx_runtime")

    def test_canonical_skill_names_and_no_duplicate_entry(self):
        names = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
        self.assertEqual(names, {"pdx", "victoria3"})
        self.assertFalse((ROOT / "skills" / "pdx-runtime").exists())
        self.assertFalse((ROOT / "skills" / "v3-runtime").exists())

    def test_all_skill_links_and_example_api_arguments(self):
        result = validator.validate(ROOT / "skills", ROOT / "src" / "pdx_runtime")
        self.assertEqual(result["status"], "PASS")
        self.assertGreater(result["python_snippets"], 0)



    def test_invented_game_method_rejected(self):
        with self.assertRaises(validator.ValidationError):
            validator.SnippetChecker(self.catalog).check(
                "await game.research.create_from_plan()", "negative method")

    def test_invented_game_parameter_rejected(self):
        with self.assertRaises(validator.ValidationError):
            validator.SnippetChecker(self.catalog).check(
                "await game.construction.build(state=1, building='building_iron_mine', approvals=True)",
                "negative parameter")

    def test_runtime_attach_is_real_but_invented_method_is_rejected(self):
        validator.SnippetChecker(self.catalog).check(
            "from pdx_runtime.session import RuntimeSession\nRuntimeSession.attach(pid)",
            "existing attach API")
        with self.assertRaises(validator.ValidationError):
            validator.SnippetChecker(self.catalog).check(
                "from pdx_runtime.session import RuntimeSession\nRuntimeSession.invented_attach_api(pid)",
                "invented runtime method")

    def test_imported_facade_and_lifecycle_parameter_resolution(self):
        validator.SnippetChecker(self.catalog).check(
            "from pdx_runtime.v3_game import V3Game\ngame = await V3Game.attach(pid=1, timeout=2)",
            "entry signature")
        validator.SnippetChecker(self.catalog).check(
            "from pdx_runtime.v3_game import V3Game\ngame = await V3Game.attach(1)",
            "positional entry signature")
        with self.assertRaises(validator.ValidationError):
            validator.SnippetChecker(self.catalog).check(
                "from pdx_runtime.v3_game import V3Game\ngame = await V3Game.attach(invented=True)",
                "negative entry parameter")


class EmbeddedGameWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_session_wraps_all_domains_without_connection(self):
        from pdx_runtime.v3_game import V3Game
        native, process = object(), Mock()
        session = SimpleNamespace(native=native, namespace={}, close=Mock())
        with patch("pdx_runtime.v3_game._core.RemoteProcess", side_effect=AssertionError("unexpected process open")):
            game = V3Game(session, process=process)
            self.assertIs(game.raw.session, session)
            self.assertIs(game.raw.native, native)
            self.assertIs(game.raw.process, process)
            for name in ("country", "economy", "research", "construction", "politics", "diplomacy", "military", "events", "time", "lifecycle"):
                self.assertIsNotNone(getattr(game, name))
            result = await game.detach()
            self.assertTrue(result["detached"])
            session.close.assert_not_called()
            process.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
