"""Offline Agent Skills checks. Never import the SDK or open a target process.

Run with the repository's Python interpreter:
  python tools/validate_agent_skills.py --sdk-src src/pdx_runtime
  python tools/validate_agent_skills.py --self-test
"""
from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path
import re
import sys
import unittest

PACKAGE = "pdx_runtime"
INPUT_TYPES = {
    "game": "pdx_runtime.v3_game._core.V3Game",
    "clock_run": "pdx_runtime.v3_game.time.TimeRun",
    "pending": "pdx_runtime.v3_game._core.PendingOperation",
    "client": "pdx_runtime.native_bridge.NativeBridge",
    "process": "pdx_runtime.native_bridge.RemoteProcess",
}
ATTRIBUTE_TYPES = {
    **{f"pdx_runtime.v3_game._core.V3Game.{name}": f"pdx_runtime.v3_game.{name}.{name.title()}API"
       for name in ("country", "economy", "research", "construction", "politics", "diplomacy", "military", "events", "time", "lifecycle")},
    "pdx_runtime.session.RuntimeSession.native": "pdx_runtime.native_bridge.NativeBridge",
    "pdx_runtime.session.RuntimeSession.client": "pdx_runtime.native_bridge.NativeBridge",
    "pdx_runtime.native_bridge.LibraryLoadPending.attempt": "pdx_runtime.native_bridge.LibraryLoadAttempt",
    "pdx_runtime.native_bridge.LibraryLoadPending.native_load": "pdx_runtime.native_bridge.LibraryLoadAttempt",
}


class ValidationError(ValueError):
    pass


def frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValidationError("missing YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValidationError("unclosed YAML frontmatter") from error
    fields = {}
    for line in lines[1:end]:
        # These skills intentionally use only single-line plain YAML strings.
        match = re.fullmatch(r"([a-z][a-z-]*): (.+)", line)
        if not match or match[1] in fields:
            raise ValidationError(f"invalid/duplicate frontmatter field: {line!r}")
        value = match[2]
        if any(token in value for token in (": ", " #", "\t")) or value[:1] in "[{&*!|>'\"%@`":
            raise ValidationError("use a plain, unambiguous single-line YAML string")
        fields[match[1]] = value
    return fields


def validate_frontmatter(path: Path, text: str) -> dict[str, str]:
    fields = frontmatter(text)
    name, description = fields.get("name", ""), fields.get("description", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        raise ValidationError(f"invalid skill name: {name!r}")
    if name != path.parent.name:
        raise ValidationError("skill name does not match directory")
    if not 1 <= len(description) <= 1024:
        raise ValidationError("description must be 1..1024 characters")
    if "Use " not in description:
        raise ValidationError("description must give concrete routing/use conditions")
    if "compatibility" in fields and not 1 <= len(fields["compatibility"]) <= 500:
        raise ValidationError("compatibility must be 1..500 characters")
    if len(text.splitlines()) > 120:
        raise ValidationError("SKILL.md exceeds this project's 120-line target")
    return fields


def code_blocks(text: str):
    pattern = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(re.findall(r"^```", text, re.MULTILINE)) != len(matches) * 2:
        raise ValidationError("unbalanced code fence")
    for match in matches:
        if match[1].strip() in ("python", "py"):
            yield text[:match.start()].count("\n") + 2, match[2]


class Catalog:
    """Resolve callables and signatures statically, including inherited methods."""
    def __init__(self, sdk: Path):
        self.nodes = {}
        self.modules = set()
        self.imports = {}
        self.class_modules = {}
        self.checked_roots = {PACKAGE}
        for path, module, source_bytes in self._sources(Path(sdk).resolve()):
            package = module if path.stem == "__init__" else module.rpartition(".")[0]
            source = source_bytes.decode("utf-8")
            tree = ast.parse(source, filename=path.name)
            self.modules.add(module)
            aliases = {}
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        base_parts = package.split(".")
                        if node.level > 1:
                            base_parts = base_parts[:-(node.level - 1)]
                        if node.module:
                            base_parts.extend(node.module.split("."))
                        base = ".".join(base_parts)
                    else:
                        base = node.module
                    for alias in node.names:
                        aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    key = f"{module}.{node.name}"
                    self.nodes[key] = (node, module, None)
                    if isinstance(node, ast.ClassDef):
                        self.class_modules[key] = module
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                self.nodes[f"{key}.{child.name}"] = (child, module, key)
            self.imports[module] = aliases

    @staticmethod
    def _module_name(destination):
        parts = list(Path(destination).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)



    def _sources(self, sdk):
        return [(path, self._module_name(PACKAGE + "/" + path.relative_to(sdk).as_posix()),
                 path.read_bytes()) for path in sorted(sdk.rglob("*.py"))]

    def tracked_name(self, name):
        return bool(name) and any(name == root or name.startswith(root + ".") for root in self.checked_roots)



    def qualify(self, node, module, local=None):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(node, ast.Name):
            if local and node.id in local:
                return local[node.id]
            if f"{module}.{node.id}" in self.nodes:
                return f"{module}.{node.id}"
            return self.imports.get(module, {}).get(node.id)
        if isinstance(node, ast.Attribute):
            prefix = self.qualify(node.value, module, local)
            return f"{prefix}.{node.attr}" if prefix else None
        return None

    def entry(self, name, seen=None):
        if name in self.nodes:
            return self.nodes[name]
        if name in self.modules:
            return None
        owner, _, method = name.rpartition(".")
        imported = self.imports.get(owner, {}).get(method)
        seen = set() if seen is None else seen
        if imported and imported not in seen:
            seen.add(imported)
            return self.entry(imported, seen)
        data = self.entry(owner, seen) if owner else None
        if data and isinstance(data[0], ast.ClassDef) and owner not in seen:
            seen.add(owner)
            canonical = f"{data[1]}.{data[0].name}.{method}"
            if canonical in self.nodes:
                return self.nodes[canonical]
            for base in data[0].bases:
                qualified = self.qualify(base, data[1])
                found = self.entry(f"{qualified}.{method}", seen) if qualified else None
                if found:
                    return found
        return None

    def signature(self, name):
        entry = self.entry(name)
        if not entry:
            raise ValidationError(f"SDK callable does not exist: {name}")
        node, module, owner = entry
        if owner == "pdx_runtime.v3_game._core.V3Game":
            if node.name in ("attach", "launch"):
                delegated = self.entry("pdx_runtime.v3_game.lifecycle._enter")
                if delegated:
                    node, module, owner = delegated
                    # game_cls is supplied by V3Game; entry callers provide no class.
                    primary = "pid" if name.endswith(".attach") else "executable"
                    pairs = list(zip(node.args.kwonlyargs, node.args.kw_defaults))
                    first, default = next((arg, value) for arg, value in pairs if arg.arg == primary)
                    others = [(arg, value) for arg, value in pairs if arg.arg not in (primary, "launching")]
                    node = ast.FunctionDef(name=node.name, args=ast.arguments(
                        posonlyargs=[], args=[first], vararg=None,
                        kwonlyargs=[arg for arg, _ in others],
                        kw_defaults=[value for _, value in others], kwarg=None, defaults=[default]),
                        body=node.body, decorator_list=[], returns=node.returns)
            elif node.name in ("new_campaign", "save", "saves", "load", "menu", "quit", "detach"):
                delegated = self.entry("pdx_runtime.v3_game.lifecycle.LifecycleAPI." + node.name)
                if delegated:
                    node, module, owner = delegated
        if isinstance(node, ast.ClassDef):
            constructor = self.entry(name + ".__init__")
            if constructor:
                node, module, owner = constructor
            else:
                # Documented dataclass constructor; field order and defaults are native source facts.
                fields = [n for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
                positional, keyword_only = [], []
                for field in fields:
                    kind = inspect.Parameter.POSITIONAL_OR_KEYWORD
                    has_default = field.value is not None
                    if (isinstance(field.value, ast.Call)
                            and self.qualify(field.value.func, module) == "dataclasses.field"):
                        options = {item.arg: item.value for item in field.value.keywords}
                        if field.value.args or None in options:
                            raise ValidationError("dataclass field options need explicit keywords")
                        for option in ("kw_only", "init"):
                            if option in options and not (isinstance(options[option], ast.Constant)
                                    and type(options[option].value) is bool):
                                raise ValidationError("dataclass field " + option + " needs a literal bool")
                        if "init" in options and options["init"].value is False:
                            continue
                        if "kw_only" in options and options["kw_only"].value is True:
                            kind = inspect.Parameter.KEYWORD_ONLY
                        has_default = "default" in options or "default_factory" in options
                    parameter = inspect.Parameter(field.target.id, kind,
                        default=object() if has_default else inspect.Parameter.empty)
                    (keyword_only if kind == inspect.Parameter.KEYWORD_ONLY else positional).append(parameter)
                return inspect.Signature([*positional, *keyword_only])
        args = node.args
        positional = [*args.posonlyargs, *args.args]
        defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
        static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)
        if owner and not static and positional and positional[0].arg in ("self", "cls"):
            positional, defaults = positional[1:], defaults[1:]
        parameters = []
        for item, default in zip(positional, defaults):
            kind = inspect.Parameter.POSITIONAL_ONLY if item in args.posonlyargs else inspect.Parameter.POSITIONAL_OR_KEYWORD
            parameters.append(inspect.Parameter(item.arg, kind,
                default=inspect.Parameter.empty if default is None else object()))
        if args.vararg:
            parameters.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
        for item, default in zip(args.kwonlyargs, args.kw_defaults):
            parameters.append(inspect.Parameter(item.arg, inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if default is None else object()))
        if args.kwarg:
            parameters.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
        return inspect.Signature(parameters)

    def returned_type(self, name, seen=None):
        entry = self.entry(name)
        if not entry:
            return None
        node, module, owner = entry
        if isinstance(node, ast.ClassDef):
            return f"{module}.{node.name}"
        qualified = self.qualify(node.returns, module) if node.returns else None
        if qualified and self.entry(qualified):
            return qualified
        seen = set() if seen is None else seen
        if name in seen:
            return None
        seen.add(name)
        # Infer direct return of cls(...) or another SDK factory, without execution.
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Call):
                if isinstance(child.value.func, ast.Name) and child.value.func.id == "cls":
                    return owner
                target = self.qualify(child.value.func, module)
                if target:
                    returned = self.returned_type(target, seen)
                    if returned:
                        return returned
        return None


class SnippetChecker:
    def __init__(self, catalog):
        self.catalog = catalog
        self.env = dict(INPUT_TYPES)
        self.calls = []
        self.imported = set()

    def symbol(self, node):
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            prefix = self.symbol(node.value)
            name = f"{prefix}.{node.attr}" if prefix else None
            if name in ATTRIBUTE_TYPES:
                return ATTRIBUTE_TYPES[name]
            # Step.submission is the actual retained original callback owner.
            if name == "pdx_runtime.v3_time_step.Step.submission":
                return "pdx_runtime.v3.CallbackResult"
            return name
        if isinstance(node, ast.Await):
            return self.symbol(node.value)
        if isinstance(node, ast.Call):
            name = self.symbol(node.func)
            return self.catalog.returned_type(name) if name else None
        return None

    def check(self, source, label):
        tree = ast.parse(source, filename=label)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = f"{node.module}.{alias.name}"
                    if not (self.catalog.tracked_name(node.module) or self.catalog.tracked_name(name)):
                        continue
                    if name not in self.catalog.modules and not self.catalog.entry(name):
                        raise ValidationError(f"{label}: SDK import does not exist: {name}")
                    self.env[alias.asname or alias.name] = name
                    self.imported.add(name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "psutil":
                        raise ValidationError(f"{label}: process-discovery dependency is not required")
                    if self.catalog.tracked_name(alias.name):
                        if alias.name not in self.catalog.modules:
                            raise ValidationError(f"{label}: SDK import does not exist: {alias.name}")
                        self.env[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                        self.imported.add(alias.name)
            elif isinstance(node, ast.ExceptHandler) and node.type and node.name:
                name = self.symbol(node.type)
                if name:
                    self.env[node.name] = name
        # Repeated propagation handles aliases and typed factory results across snippets.
        for _ in range(6):
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    value = self.symbol(node.value)
                    if value:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                self.env[target.id] = value
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = self.symbol(node.func)
            # Respect explicit isinstance narrowing for resumable entry examples.
            # The game variable becomes PendingOperation only within that branch.
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                scope = parents.get(node)
                while scope is not None:
                    test = scope.test if isinstance(scope, (ast.If, ast.While)) else None
                    if (isinstance(test, ast.Call) and isinstance(test.func, ast.Name)
                            and test.func.id == "isinstance" and len(test.args) == 2
                            and isinstance(test.args[0], ast.Name)
                            and test.args[0].id == node.func.value.id):
                        narrowed = self.symbol(test.args[1])
                        if narrowed and self.catalog.entry(narrowed):
                            name = narrowed + "." + node.func.attr
                            break
                    scope = parents.get(scope)
            if not self.catalog.tracked_name(name):
                continue
            signature = self.catalog.signature(name)
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                raise ValidationError(f"{label}: use explicit arguments in documentation snippets")
            try:
                signature.bind(*([None] * len(node.args)), **{k.arg: None for k in node.keywords})
            except TypeError as error:
                raise ValidationError(f"{label}:{node.lineno}: {name}{signature}: {error}") from error
            self.calls.append({"api": name, "keywords": [k.arg for k in node.keywords]})
        return len(list(ast.walk(tree)))


def validate(skills, sdk):
    if not sdk.is_dir():
        raise ValidationError("SDK source directory is missing; pass --sdk-src")
    catalog = Catalog(sdk)
    skill_files = sorted(skills.glob("*/SKILL.md"))
    if {p.parent.name for p in skill_files} != {"pdx", "victoria3"}:
        raise ValidationError("expected exactly skills/pdx and skills/victoria3")
    result = {"status": "PASS", "skills": [], "files": 0, "python_snippets": 0,
              "sdk_calls": [],
              "scope": "Offline format/link/Python AST/signature checks only; no live native validation."}
    for path in skill_files:
        text = path.read_text(encoding="utf-8")
        fields = validate_frontmatter(path, text)
        result["skills"].append({"name": fields["name"], "lines": len(text.splitlines())})
        docs = [path, *sorted((path.parent / "references").glob("*.md"))]
        links_from_root = []
        for document in docs:
            text = document.read_text(encoding="utf-8")
            result["files"] += 1
            if re.search(r"(?i)([A-Z]:[/\\]Users[/\\]|actual11956|0x[0-9a-f]{10,})", text):
                raise ValidationError(f"{document.name}: private home path/ASLR evidence in deliverable")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if re.match(r"[a-z]+://", target) or target.startswith("#"):
                    continue
                link = (document.parent / target.split("#", 1)[0]).resolve()
                if not link.is_file() or not link.is_relative_to(skills.resolve()):
                    raise ValidationError(f"{document.name}: broken/outside-skill link: {target}")
                if document == path:
                    links_from_root.append(link)
            checker = SnippetChecker(catalog)
            for line, source in code_blocks(text):
                checker.check(source, f"{document.relative_to(skills).as_posix()}:{line}")
                result["python_snippets"] += 1
            result["sdk_calls"].extend(checker.calls)
        references = set((path.parent / "references").glob("*.md"))
        if {p.resolve() for p in references} - set(links_from_root):
            raise ValidationError(f"{path.parent.name}: reference not directly linked from SKILL.md")
    return result


class ValidatorSelfTests(unittest.TestCase):
    def test_frontmatter_valid(self):
        text = "---\nname: pdx\ndescription: Use when testing native calls.\n---\n# Test\n"
        self.assertEqual(validate_frontmatter(Path("pdx/SKILL.md"), text)["name"], "pdx")

    def test_bad_name_rejected(self):
        for name in ("PDX", "pdx--runtime", "-pdx", "pdx-", "pdx_runtime"):
            text = f"---\nname: {name}\ndescription: Use when testing.\n---\n"
            with self.assertRaises(ValidationError):
                validate_frontmatter(Path(name) / "SKILL.md", text)

    def test_missing_description_rejected(self):
        with self.assertRaises(ValidationError):
            validate_frontmatter(Path("pdx/SKILL.md"), "---\nname: pdx\n---\n")

    def test_python_syntax_checked(self):
        with self.assertRaises(SyntaxError):
            ast.parse("session = (")

    def test_code_fence_checked(self):
        with self.assertRaises(ValidationError):
            list(code_blocks("```python\na = 1\n"))



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--skills", type=Path, default=root / "skills")
    parser.add_argument("--sdk-src", type=Path, default=root / "src" / PACKAGE)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ValidatorSelfTests)
        run = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if run.wasSuccessful() else 1
    try:
        result = validate(args.skills.resolve(), args.sdk_src.resolve())
        # Prove the source-bound checker rejects an invented keyword and callable.
        catalog = Catalog(args.sdk_src)
        negative = (
            "from pdx_runtime.session import RuntimeSession\nRuntimeSession.load(pid, invented=True)",
            "from pdx_runtime.session import RuntimeSession\nRuntimeSession.invented_attach_api(pid)",
        )
        for source in negative:
            try:
                SnippetChecker(catalog).check(source, "negative control")
            except ValidationError:
                pass
            else:
                raise ValidationError("API negative control unexpectedly passed")
        result["api_negative_controls"] = len(negative)
        rendered = json.dumps(result, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    except (ValidationError, SyntaxError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
