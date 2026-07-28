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
TEST_FILE_PATTERN = re.compile(r"(?:^|/)test_[^/]+\.py$")
HUNK_PATTERN = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@"
)
HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
SOURCE_ROOT_MARKERS = ("agentsassemble/", "frontend/src/", "scripts/")
KNOWN_RULES = frozenset(
    {
        "exact_ui_copy",
        "mock_only",
        "no_oracle",
        "private_patch",
        "source_text",
        "symbol_only",
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


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


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


def _assertion_calls(node: ast.AST) -> tuple[ast.Call, ...]:
    calls = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child)
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


def _helper_has_oracle(
    helper_name: str,
    helpers: Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> bool:
    if helper_name in seen or helper_name not in helpers:
        return False
    seen.add(helper_name)
    helper = helpers[helper_name]
    if any(isinstance(child, ast.Assert) for child in ast.walk(helper)):
        return True
    if _assertion_calls(helper) or _subprocess_check_call(helper):
        return True
    return any(
        _helper_has_oracle(child.func.attr, helpers, seen)
        for child in ast.walk(helper)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "self"
    )


def _has_oracle(
    test: TestFunction,
    helpers_by_class: Mapping[str, Mapping[str, ast.FunctionDef | ast.AsyncFunctionDef]],
) -> bool:
    if any(isinstance(child, ast.Assert) for child in ast.walk(test.node)):
        return True
    if _assertion_calls(test.node) or _subprocess_check_call(test.node):
        return True
    helpers = helpers_by_class.get(test.class_name, {})
    return any(
        _helper_has_oracle(child.func.attr, helpers, set())
        for child in ast.walk(test.node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and isinstance(child.func.value, ast.Name)
        and child.func.value.id == "self"
    )


def _private_patch_targets(node: ast.AST) -> tuple[tuple[int, str], ...]:
    targets: list[tuple[int, str]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child)
        if name not in {"patch", "object"}:
            continue
        candidate: ast.AST | None = None
        if name == "patch" and child.args:
            candidate = child.args[0]
        elif name == "object" and len(child.args) >= 2:
            candidate = child.args[1]
        if not isinstance(candidate, ast.Constant) or not isinstance(candidate.value, str):
            continue
        target = candidate.value
        final_name = target.rsplit(".", 1)[-1]
        if target.startswith("agentsassemble.") and final_name.startswith("_"):
            targets.append((child.lineno, target))
        elif name == "object" and final_name.startswith("_") and not final_name.startswith("__"):
            targets.append((child.lineno, target))
    return tuple(targets)


def _mock_only_oracle(node: ast.AST) -> bool:
    assertions = _assertion_calls(node)
    if not assertions:
        return False
    has_mock = any(
        isinstance(child, ast.Call)
        and _call_name(child) in {"patch", "Mock", "MagicMock", "AsyncMock"}
        for child in ast.walk(node)
    )
    if not has_mock:
        return False
    mock_assertion_names = {
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
    return all(_call_name(call) in mock_assertion_names for call in assertions)


def _looks_like_production_source_read(call: ast.Call) -> bool:
    if _call_name(call) not in {"read_text", "read"}:
        return False
    rendered = ast.unparse(call)
    if any(marker in rendered for marker in SOURCE_ROOT_MARKERS):
        return True
    fragments = {
        child.value.strip("/")
        for child in ast.walk(call)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }
    return "agentsassemble" in fragments or (
        "frontend" in fragments and "src" in fragments
    )


def _production_source_variables(node: ast.AST) -> frozenset[str]:
    variables: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):
            continue
        value = child.value
        if not isinstance(value, ast.Call) or not _looks_like_production_source_read(value):
            continue
        targets = child.targets if isinstance(child, ast.Assign) else (child.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                variables.add(target.id)
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
    for call in _assertion_calls(node):
        name = _call_name(call)
        if name not in {"assertEqual", "assertMultiLineEqual"} or len(call.args) < 2:
            continue
        for candidate in call.args[:2]:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                if HANGUL_PATTERN.search(candidate.value):
                    return call.lineno, candidate.value
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        script = child.value
        if "node:assert" not in script:
            continue
        match = re.search(
            r"assert\.equal\([^;]+?,\s*([\"'`])(?P<value>.*?[\uac00-\ud7a3].*?)\1\s*\)",
            script,
            flags=re.DOTALL,
        )
        if match:
            return child.lineno, match.group("value")
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

        if not _has_oracle(test, helpers):
            add(
                "no_oracle",
                test.node.lineno,
                "The test has no observable assertion, expected failure, or checked subprocess.",
            )
        if _source_text_assertion(test.node):
            add(
                "source_text",
                test.node.lineno,
                "The test inspects implementation source text instead of executing behavior.",
            )
        for line, target in _private_patch_targets(test.node):
            add(
                "private_patch",
                line,
                f"The test patches the private production target {target!r}.",
            )
        if _mock_only_oracle(test.node):
            add(
                "mock_only",
                test.node.lineno,
                "Every oracle is a mock interaction; assert a result, state change, or side effect.",
            )
        exact_copy = _exact_hangul_assertion(test.node)
        if exact_copy:
            line, value = exact_copy
            add(
                "exact_ui_copy",
                line,
                f"The test freezes exact Korean copy {value!r} instead of a behavior contract.",
            )
        if _symbol_only_test(test.node):
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
) -> Mapping[str, frozenset[int]]:
    merge_base = _valid_base(base, repository_root)
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
    changed: dict[str, set[int]] = {}
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            candidate = line[6:]
            current_path = candidate if TEST_FILE_PATTERN.search(candidate) else ""
            continue
        match = HUNK_PATTERN.match(line)
        if not current_path or not match:
            continue
        start = int(match.group("start"))
        count = int(match.group("count") or "1")
        changed.setdefault(current_path, set()).update(range(start, start + count))
    untracked = _git_output(
        ["ls-files", "--others", "--exclude-standard", "--", "tests"],
        repository_root,
    )
    for relative_path in untracked.splitlines():
        if not TEST_FILE_PATTERN.search(relative_path):
            continue
        path = repository_root / relative_path
        changed[relative_path] = set(range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1))
    return {path: frozenset(lines) for path, lines in changed.items()}


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
        for path in sorted((repository_root / "tests").rglob("test_*.py"))
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
            source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
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
