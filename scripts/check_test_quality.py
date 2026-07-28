#!/usr/bin/env python3
"""Reject newly added shallow Python tests before they enter the suite."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Iterable, Iterator, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEPTIONS_PATH = REPOSITORY_ROOT / "tests" / "test_quality_exceptions.toml"
HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
HUNK_PATTERN = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@"
)
SOURCE_ROOT_MARKERS = ("agentsassemble/", "frontend/src/", "scripts/")
KNOWN_RULES = frozenset(
    {
        "exact_ui_copy",
        "mock_only",
        "no_oracle",
        "private_patch",
        "source_text",
        "symbol_only",
        "tautological",
    }
)

MOCK_ASSERTION_NAMES = frozenset(
    {
        "assert_called",
        "assert_called_once",
        "assert_called_once_with",
        "assert_called_with",
        "assert_not_called",
        "assert_awaited",
        "assert_awaited_once",
        "assert_awaited_once_with",
        "assert_awaited_with",
        "assert_not_awaited",
    }
)
MOCK_OBSERVATION_ATTRIBUTES = frozenset(
    {
        "await_args",
        "await_args_list",
        "await_count",
        "call_args",
        "call_args_list",
        "call_count",
        "called",
        "mock_calls",
    }
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    test_id: str
    rule: str
    message: str


@dataclass(frozen=True)
class TestFunction:
    path: str
    class_name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef

    @property
    def test_id(self) -> str:
        module = self.path.removesuffix(".py").replace("/", ".")
        parts = [module]
        if self.class_name:
            parts.append(self.class_name)
        parts.append(self.node.name)
        return ".".join(parts)


def _call_name(call: ast.Call, aliases: Mapping[str, str] | None = None) -> str:
    if isinstance(call.func, ast.Name):
        name = call.func.id
    elif isinstance(call.func, ast.Attribute):
        name = call.func.attr
    else:
        return ""
    return (aliases or {}).get(name, name)


def _import_aliases(tree: ast.Module) -> Mapping[str, str]:
    aliases: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ImportFrom):
            continue
        for imported in statement.names:
            aliases[imported.asname or imported.name] = imported.name
    return aliases


def _call_aliases(node: ast.AST) -> Mapping[str, str]:
    aliases: dict[str, str] = {}
    for child in ast.walk(node):
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):
            continue
        value = child.value
        if not isinstance(value, ast.Attribute) or value.attr not in MOCK_ASSERTION_NAMES:
            continue
        targets = child.targets if isinstance(child, ast.Assign) else (child.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = value.attr
    return aliases


def _iter_test_functions(path: str, tree: ast.Module) -> Iterator[TestFunction]:
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name.startswith("test_"):
                yield TestFunction(path, "", statement)
            continue
        if not isinstance(statement, ast.ClassDef):
            continue
        for child in statement.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith(
                "test_"
            ):
                yield TestFunction(path, statement.name, child)


def _class_helpers(
    tree: ast.Module,
) -> Mapping[str, Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    helpers: dict[str, dict[str, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        helpers[statement.name] = {
            child.name: child
            for child in statement.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not child.name.startswith("test_")
        }
    return helpers


def _module_helpers(
    tree: ast.Module,
) -> Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        statement.name: statement
        for statement in tree.body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not statement.name.startswith("test_")
    }


def _reachable_test_nodes(
    test: TestFunction,
    helpers_by_class: Mapping[str, Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]],
    module_helpers: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    helpers = helpers_by_class.get(test.class_name, {})
    pending: list[ast.FunctionDef | ast.AsyncFunctionDef] = [test.node]
    if test.class_name:
        pending.extend(
            helper
            for name in ("setUp", "asyncSetUp", "setUpClass")
            if (helper := helpers.get(name)) is not None
        )
    seen: set[str] = set()
    reachable: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    while pending:
        node = pending.pop()
        identity = f"{node.name}:{node.lineno}"
        if identity in seen:
            continue
        seen.add(identity)
        reachable.append(node)
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
            ):
                helper = helpers.get(child.func.attr)
                if helper is not None:
                    pending.append(helper)
            elif isinstance(child.func, ast.Name):
                helper = module_helpers.get(child.func.id)
                if helper is not None:
                    pending.append(helper)
    return tuple(reachable)


def _assertion_calls(
    node: ast.AST,
    import_aliases: Mapping[str, str] | None = None,
) -> tuple[ast.Call, ...]:
    aliases = {**(import_aliases or {}), **_call_aliases(node)}
    calls = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child, aliases)
        if name.startswith("assert") or name in {"raises", "fail"}:
            calls.append(child)
    return tuple(calls)


def _subprocess_check_call(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if not isinstance(child.func.value, ast.Name) or child.func.value.id != "subprocess":
            continue
        if child.func.attr not in {"run", "check_call", "check_output"}:
            continue
        if child.func.attr in {"check_call", "check_output"}:
            return True
        if any(
            keyword.arg == "check"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in child.keywords
        ):
            return True
    return False


def _has_oracle(
    nodes: Iterable[ast.AST],
    import_aliases: Mapping[str, str],
) -> bool:
    return any(
        any(isinstance(child, ast.Assert) for child in ast.walk(node))
        or bool(_assertion_calls(node, import_aliases))
        or _subprocess_check_call(node)
        for node in nodes
    )


def _string_constants(node: ast.AST) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for child in ast.walk(node):
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):
            continue
        if not isinstance(child.value, ast.Constant) or not isinstance(child.value.value, str):
            continue
        targets = child.targets if isinstance(child, ast.Assign) else (child.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = child.value.value
    return values


def _resolved_string(node: ast.AST | None, constants: Mapping[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    return ""


def _private_patch_targets(
    node: ast.AST,
    import_aliases: Mapping[str, str],
) -> tuple[tuple[int, str], ...]:
    targets: list[tuple[int, str]] = []
    constants = _string_constants(node)
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child, import_aliases)
        if name not in {"patch", "object"}:
            continue
        candidate: ast.AST | None = None
        if name == "patch" and child.args:
            candidate = child.args[0]
        elif name == "object" and len(child.args) >= 2:
            candidate = child.args[1]
        target = _resolved_string(candidate, constants)
        if not target:
            continue
        final_name = target.rsplit(".", 1)[-1]
        if target.startswith("agentsassemble.") and final_name.startswith("_"):
            targets.append((child.lineno, target))
        elif name == "object" and final_name.startswith("_") and not final_name.startswith("__"):
            targets.append((child.lineno, target))
    return tuple(targets)


def _mock_only_oracle(
    nodes: Iterable[ast.AST],
    import_aliases: Mapping[str, str],
) -> bool:
    node_list = tuple(nodes)
    assertions = tuple(
        (
            assertion,
            _call_name(
                assertion,
                {**import_aliases, **_call_aliases(node)},
            ),
        )
        for node in node_list
        for assertion in _assertion_calls(node, import_aliases)
    )
    bare_assertions = tuple(
        child
        for node in node_list
        for child in ast.walk(node)
        if isinstance(child, ast.Assert)
    )
    if not assertions and not bare_assertions:
        return False

    def references_mock_observation(node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Attribute)
            and child.attr in MOCK_OBSERVATION_ATTRIBUTES
            for child in ast.walk(node)
        )

    def is_mock_call_observation(call: ast.Call, name: str) -> bool:
        if name in MOCK_ASSERTION_NAMES:
            return True
        return references_mock_observation(call)

    return all(
        is_mock_call_observation(call, name)
        for call, name in assertions
    ) and all(
        references_mock_observation(assertion.test)
        for assertion in bare_assertions
    )


def _tautological_assertion(
    nodes: Iterable[ast.AST],
    import_aliases: Mapping[str, str],
) -> int | None:
    equality_assertions = {
        "assertEqual",
        "assertIs",
        "assertListEqual",
        "assertMultiLineEqual",
        "assertSequenceEqual",
        "assertSetEqual",
        "assertTupleEqual",
    }

    def stable_expression(node: ast.AST) -> bool:
        return not any(
            isinstance(child, (ast.Await, ast.Call, ast.Yield, ast.YieldFrom))
            for child in ast.walk(node)
        )

    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                if isinstance(child.test, ast.Constant) and bool(child.test.value):
                    return child.lineno
                if (
                    isinstance(child.test, ast.Compare)
                    and len(child.test.comparators) == 1
                    and ast.dump(child.test.left) == ast.dump(child.test.comparators[0])
                    and stable_expression(child.test.left)
                ):
                    return child.lineno
        aliases = {**import_aliases, **_call_aliases(node)}
        for call in _assertion_calls(node, import_aliases):
            name = _call_name(call, aliases)
            if name == "assertTrue" and call.args:
                value = call.args[0]
                if isinstance(value, ast.Constant) and bool(value.value):
                    return call.lineno
            if name == "assertFalse" and call.args:
                value = call.args[0]
                if isinstance(value, ast.Constant) and not bool(value.value):
                    return call.lineno
            if (
                name in equality_assertions
                and len(call.args) >= 2
                and ast.dump(call.args[0]) == ast.dump(call.args[1])
                and stable_expression(call.args[0])
            ):
                return call.lineno
    return None


def _looks_like_production_path(
    node: ast.AST,
    known_paths: Iterable[str] = (),
) -> bool:
    if _references_any(node, known_paths):
        return True
    rendered = ast.unparse(node)
    if any(marker in rendered for marker in SOURCE_ROOT_MARKERS):
        return True
    fragments = {
        child.value.strip("/")
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    return "agentsassemble" in fragments or (
        "frontend" in fragments and "src" in fragments
    )


def _looks_like_production_source_read(
    call: ast.Call,
    known_paths: Iterable[str] = (),
) -> bool:
    if _call_name(call) not in {"read_text", "read"}:
        return False
    return _looks_like_production_path(call, known_paths)


def _production_source_variables(node: ast.AST) -> frozenset[str]:
    assignments = tuple(
        child
        for child in ast.walk(node)
        if isinstance(child, (ast.Assign, ast.AnnAssign))
    )
    path_variables: set[str] = set()
    variables: set[str] = set()
    changed = True
    while changed:
        changed = False
        for child in assignments:
            targets = child.targets if isinstance(child, ast.Assign) else (child.target,)
            names = {
                target.id
                for target in targets
                if isinstance(target, ast.Name)
            }
            value = child.value
            if isinstance(value, ast.Call) and _looks_like_production_source_read(
                value,
                path_variables,
            ):
                new_variables = names - variables
                variables.update(new_variables)
                changed = changed or bool(new_variables)
                continue
            if _looks_like_production_path(value, path_variables):
                new_paths = names - path_variables
                path_variables.update(new_paths)
                changed = changed or bool(new_paths)
    return frozenset(variables)


def _references_any(node: ast.AST, names: Iterable[str]) -> bool:
    expected = frozenset(names)
    return any(
        isinstance(child, ast.Name) and child.id in expected for child in ast.walk(node)
    )


def _source_text_assertion(node: ast.AST) -> bool:
    source_variables = _production_source_variables(node)
    for assertion in _assertion_calls(node):
        if source_variables and _references_any(assertion, source_variables):
            return True
        if any(
            isinstance(child, ast.Call) and _looks_like_production_source_read(child)
            for child in ast.walk(assertion)
        ):
            return True
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        script = child.value
        if "node:assert" not in script or "readFile" not in script:
            continue
        if not any(marker in script for marker in SOURCE_ROOT_MARKERS):
            continue
        if re.search(r"(?:source|text|code)\.(?:includes|match|indexOf)", script):
            return True
        if re.search(r"assert\.(?:match|ok)\(\s*(?:source|text|code)", script):
            return True
    return False


def _exact_hangul_assertion(node: ast.AST) -> tuple[int, str] | None:
    copy_identifiers = (
        "button",
        "copy",
        "description",
        "error_message",
        "label",
        "login_required",
        "placeholder",
        "title",
        "tooltip",
        "user_message",
    )

    def is_copy_expression(candidate: ast.AST) -> bool:
        identifiers = [
            child.id
            for child in ast.walk(candidate)
            if isinstance(child, ast.Name)
        ]
        identifiers.extend(
            child.attr
            for child in ast.walk(candidate)
            if isinstance(child, ast.Attribute)
        )
        identifiers.extend(
            str(child.value)
            for child in ast.walk(candidate)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        )
        return any(
            marker in identifier.lower()
            for identifier in identifiers
            for marker in copy_identifiers
        )

    def copy_assertion(call: ast.Call) -> tuple[int, str] | None:
        name = _call_name(call)
        if name not in {"assertEqual", "assertMultiLineEqual"} or len(call.args) < 2:
            return None
        for index, candidate in enumerate(call.args[:2]):
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                other = call.args[1 - index]
                if HANGUL_PATTERN.search(candidate.value) and is_copy_expression(other):
                    return call.lineno, candidate.value
        return None

    def is_subprocess_success_boilerplate(call: ast.Call) -> bool:
        if _call_name(call) not in {"assertEqual", "assertFalse"}:
            return False
        return any(
            isinstance(child, ast.Attribute) and child.attr == "returncode"
            for child in ast.walk(call)
        )

    assertions = _assertion_calls(node)
    copies = tuple(
        copy
        for call in assertions
        if (copy := copy_assertion(call)) is not None
    )
    if copies and not any(
        copy_assertion(call) is None and not is_subprocess_success_boilerplate(call)
        for call in assertions
    ):
        return copies[0]
    return None


def _symbol_only_test(node: ast.AST) -> bool:
    assertions = _assertion_calls(node)
    if not assertions:
        return False

    def is_symbol_assertion(call: ast.Call) -> bool:
        name = _call_name(call)
        if name in {"assertIs", "assertIsNot"} and len(call.args) >= 2:
            return all(isinstance(arg, (ast.Name, ast.Attribute)) for arg in call.args[:2])
        if name not in {"assertTrue", "assertFalse"} or not call.args:
            return False
        value = call.args[0]
        return (
            isinstance(value, ast.Call)
            and _call_name(value) in {"callable", "hasattr"}
        )

    return all(is_symbol_assertion(call) for call in assertions)


def analyze_python_test_source(
    path: str,
    source: str,
    *,
    changed_lines: frozenset[int] | None = None,
    exceptions: Mapping[str, frozenset[str]] | None = None,
) -> tuple[Violation, ...]:
    tree = ast.parse(source, filename=path)
    helpers = _class_helpers(tree)
    module_helpers = _module_helpers(tree)
    import_aliases = _import_aliases(tree)
    allowed = exceptions or {}
    violations: list[Violation] = []
    for test in _iter_test_functions(path, tree):
        if changed_lines is not None and not any(
            test.node.lineno <= line <= (test.node.end_lineno or test.node.lineno)
            for line in changed_lines
        ):
            continue

        def add(rule: str, line: int, message: str) -> None:
            if rule in allowed.get(test.test_id, frozenset()):
                return
            violations.append(Violation(path, line, test.test_id, rule, message))

        reachable_nodes = _reachable_test_nodes(test, helpers, module_helpers)
        if not _has_oracle(reachable_nodes, import_aliases):
            add(
                "no_oracle",
                test.node.lineno,
                "The test has no observable assertion, expected failure, or checked subprocess.",
            )
        if any(_source_text_assertion(node) for node in reachable_nodes):
            add(
                "source_text",
                test.node.lineno,
                "The test inspects implementation source text instead of executing behavior.",
            )
        private_targets = {
            (line, target)
            for node in reachable_nodes
            for line, target in _private_patch_targets(node, import_aliases)
        }
        for line, target in sorted(private_targets):
            add("private_patch", line, f"The test patches the private production target {target!r}.")
        if _mock_only_oracle(reachable_nodes, import_aliases):
            add(
                "mock_only",
                test.node.lineno,
                "Every oracle is a mock interaction; assert a result, state change, or side effect.",
            )
        tautology_line = _tautological_assertion(reachable_nodes, import_aliases)
        if tautology_line is not None:
            add(
                "tautological",
                tautology_line,
                "The assertion is true independently of the behavior under test.",
            )
        exact_copy = next(
            (
                exact_copy
                for node in reachable_nodes
                if (exact_copy := _exact_hangul_assertion(node))
            ),
            None,
        )
        if exact_copy:
            line, value = exact_copy
            add(
                "exact_ui_copy",
                line,
                f"The test freezes exact Korean copy {value!r} instead of a behavior contract.",
            )
        if all(
            _symbol_only_test(node)
            for node in reachable_nodes
            if _assertion_calls(node, import_aliases)
        ) and any(_assertion_calls(node, import_aliases) for node in reachable_nodes):
            add(
                "symbol_only",
                test.node.lineno,
                "The test only checks symbol identity/existence without exercising a consumer.",
            )
    return tuple(violations)


def _git_output(arguments: list[str], repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _valid_base(base: str, repository_root: Path) -> str:
    normalized = base.strip()
    if not normalized or set(normalized) == {"0"}:
        normalized = "HEAD^"
    try:
        _git_output(["rev-parse", "--verify", f"{normalized}^{{commit}}"], repository_root)
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Test-quality base commit is unavailable: {normalized}") from error
    return _git_output(["merge-base", normalized, "HEAD"], repository_root).strip()


def changed_python_test_lines(
    repository_root: Path,
    *,
    base: str,
) -> Mapping[str, frozenset[int] | None]:
    """Select changed Python tests without losing support or helper edits.

    Direct edits inside a test body select that body. Any helper, setup,
    fixture, import, support-contract, or deletion-only change selects the
    whole surviving file because it can alter multiple tests.
    """
    merge_base = _valid_base(base, repository_root)
    name_status = _git_output(
        [
            "diff",
            "--name-status",
            "--no-ext-diff",
            "--no-renames",
            merge_base,
            "--",
            "tests",
        ],
        repository_root,
    )
    candidates: set[str] = set()
    deleted: set[str] = set()
    for line in name_status.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, candidate = parts
        if not candidate.endswith(".py"):
            continue
        candidates.add(candidate)
        if status.startswith("D"):
            deleted.add(candidate)

    diff = _git_output(
        [
            "diff",
            "--unified=0",
            "--no-ext-diff",
            "--no-renames",
            merge_base,
            "--",
            "tests",
        ],
        repository_root,
    )
    added_lines: dict[str, set[int]] = {path: set() for path in candidates}
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current_path = ""
            continue
        if line.startswith("+++ b/"):
            candidate = line[6:]
            current_path = candidate if candidate in candidates else ""
            continue
        if line == "+++ /dev/null":
            current_path = ""
            continue
        match = HUNK_PATTERN.match(line)
        if not current_path or not match:
            continue
        start = int(match.group("start"))
        count = int(match.group("count") or "1")
        added_lines[current_path].update(range(start, start + count))

    changed: dict[str, frozenset[int] | None] = {}
    for relative_path in sorted(candidates):
        path = repository_root / relative_path
        if relative_path in deleted or not path.is_file():
            changed[relative_path] = frozenset()
            continue
        lines = added_lines.get(relative_path, set())
        if not lines or not path.name.startswith("test_"):
            changed[relative_path] = None
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        test_ranges = tuple(
            (
                min(
                    (decorator.lineno for decorator in test.node.decorator_list),
                    default=test.node.lineno,
                ),
                test.node.end_lineno or test.node.lineno,
            )
            for test in _iter_test_functions(relative_path, tree)
        )
        if all(any(start <= line <= end for start, end in test_ranges) for line in lines):
            changed[relative_path] = frozenset(lines)
        else:
            changed[relative_path] = None

    untracked = _git_output(
        ["ls-files", "--others", "--exclude-standard", "--", "tests"],
        repository_root,
    )
    for relative_path in untracked.splitlines():
        if not relative_path.endswith(".py"):
            continue
        changed[relative_path] = None
    return changed


def load_exceptions(path: Path) -> Mapping[str, frozenset[str]]:
    if not path.exists():
        return {}
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    allowed: dict[str, frozenset[str]] = {}
    for item in payload.get("allow", []):
        test_id = str(item.get("test_id") or "").strip()
        rules = frozenset(str(rule).strip() for rule in item.get("rules", []) if str(rule).strip())
        reason = str(item.get("reason") or "").strip()
        if not test_id or not rules or len(reason) < 20:
            raise ValueError(
                "Every test-quality exception needs test_id, rules, and a specific reason "
                "of at least 20 characters."
            )
        unknown_rules = rules - KNOWN_RULES
        if unknown_rules:
            raise ValueError(
                "Unknown test-quality exception rule(s): "
                + ", ".join(sorted(unknown_rules))
            )
        if test_id in allowed:
            raise ValueError(f"Duplicate test-quality exception for {test_id}.")
        allowed[test_id] = rules
    return allowed


def _all_python_tests(repository_root: Path) -> Mapping[str, frozenset[int] | None]:
    return {
        path.relative_to(repository_root).as_posix(): None
        for path in sorted((repository_root / "tests").rglob("*.py"))
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject shallow newly added or modified Python tests."
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("TEST_QUALITY_BASE", "HEAD"),
        help="Git revision used to select changed tests (default: TEST_QUALITY_BASE or HEAD).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Audit every Python test instead of only tests changed since --base.",
    )
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=DEFAULT_EXCEPTIONS_PATH,
        help="TOML file containing reviewed structural-contract exceptions.",
    )
    args = parser.parse_args()
    try:
        exceptions = load_exceptions(args.exceptions)
        selected = (
            _all_python_tests(REPOSITORY_ROOT)
            if args.all
            else changed_python_test_lines(REPOSITORY_ROOT, base=args.base)
        )
        violations: list[Violation] = []
        for relative_path, changed_lines in selected.items():
            test_path = REPOSITORY_ROOT / relative_path
            if not test_path.is_file():
                continue
            source = test_path.read_text(encoding="utf-8")
            violations.extend(
                analyze_python_test_source(
                    relative_path,
                    source,
                    changed_lines=changed_lines,
                    exceptions=exceptions,
                )
            )
    except (OSError, SyntaxError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Test-quality gate could not complete: {error}", file=sys.stderr)
        return 2

    if not violations:
        print(f"Test-quality gate passed for {len(selected)} selected Python test file(s).")
        return 0

    print("Shallow selected tests were rejected:", file=sys.stderr)
    for violation in sorted(
        violations,
        key=lambda item: (item.path, item.line, item.rule),
    ):
        print(
            f"- {violation.path}:{violation.line} [{violation.rule}] "
            f"{violation.test_id}: {violation.message}",
            file=sys.stderr,
        )
    try:
        exception_label = args.exceptions.relative_to(REPOSITORY_ROOT)
    except ValueError:
        exception_label = args.exceptions
    print(
        "Exercise the real behavior, or add a narrowly reviewed entry to "
        f"{exception_label}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
