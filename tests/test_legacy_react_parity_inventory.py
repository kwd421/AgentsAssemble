import re
import unittest
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, order=True)
class Route:
    path: str
    method: str
    handler_form: str


@dataclass(frozen=True)
class MatrixRoute:
    route: Route
    react_wrapper: str
    react_wired: str


class LegacyReactParityInventoryTests(unittest.TestCase):
    def test_gui_routes_match_matrix_appendix(self):
        gui_routes = _parse_gui_routes(ROOT / "agentsassemble" / "gui.py")
        matrix_routes = {entry.route for entry in _parse_matrix_appendix(ROOT / "docs" / "product" / "legacy-react-parity-matrix.md")}

        self.assertEqual(gui_routes, matrix_routes)

    def test_appendix_react_wrappers_resolve_in_api_ts(self):
        entries = _parse_matrix_appendix(ROOT / "docs" / "product" / "legacy-react-parity-matrix.md")
        api_wrappers_by_route = _parse_api_ts_wrappers_by_route(ROOT / "frontend" / "src" / "api.ts")

        mismatched_wrappers: list[str] = []
        wrongly_labeled: list[str] = []
        for entry in entries:
            expected_wrappers = set(_wrapper_names(entry.react_wrapper))
            react_wired = entry.react_wired.casefold()
            if react_wired == "yes":
                actual_wrappers = api_wrappers_by_route.get(entry.route, set())
                if expected_wrappers != actual_wrappers:
                    mismatched_wrappers.append(
                        f"{entry.route.method} {entry.route.path} expected {sorted(expected_wrappers)} "
                        f"from appendix but the API modules have {sorted(actual_wrappers)}"
                    )
            elif expected_wrappers:
                wrongly_labeled.append(
                    f"{entry.route.method} {entry.route.path} is {entry.react_wired} but lists "
                    f"{sorted(expected_wrappers)}"
                )

        self.assertEqual([], mismatched_wrappers)
        self.assertEqual([], wrongly_labeled)

    def test_api_ts_endpoints_covered_by_react_wired_appendix_rows(self):
        api_routes = _parse_api_ts_routes(ROOT / "frontend" / "src" / "api.ts")
        react_wired_matrix_routes = {
            entry.route
            for entry in _parse_matrix_appendix(ROOT / "docs" / "product" / "legacy-react-parity-matrix.md")
            if entry.react_wired.casefold() == "yes"
        }

        self.assertEqual(api_routes, react_wired_matrix_routes)

    def test_surface_inventory_api_wrapper_names_exist(self):
        matrix_path = ROOT / "docs" / "product" / "legacy-react-parity-matrix.md"
        surface_wrappers = _parse_surface_inventory_api_wrapper_names(matrix_path)
        api_wrappers = _parse_api_ts_exported_functions(ROOT / "frontend" / "src" / "api.ts")

        missing = sorted(name for name in surface_wrappers if name not in api_wrappers)
        self.assertEqual([], missing)

    def test_modular_api_routes_report_their_concrete_owner_modules(self):
        owners = _parse_api_ts_route_owners(ROOT / "frontend" / "src" / "api.ts")

        self.assertEqual("roomHistory.ts", owners[Route("/api/attachments", "POST", "exact")].name)
        self.assertEqual("invites.ts", owners[Route("/api/room-invite/create", "POST", "exact")].name)
        self.assertEqual("agentSessions.ts", owners[Route("/api/agent-sessions/resume", "POST", "exact")].name)

    def test_default_and_react_surface_labels_are_documented(self):
        matrix_text = (ROOT / "docs" / "product" / "legacy-react-parity-matrix.md").read_text(encoding="utf-8")

        self.assertIn("Discord-style room client (default entry point)", matrix_text)
        self.assertIn("legacy static routes are retired", matrix_text)
        self.assertIn("/legacy/", matrix_text)


def _parse_router_module_routes(module_path: Path) -> set[Route]:
    """Routes registered on the R2 route table (@router.get/post/delete)."""
    routes: set[Route] = set()
    for line in module_path.read_text(encoding="utf-8").splitlines():
        match = re.search(
            r'@router\.(get|post|delete|get_dynamic|post_dynamic)\("(/api/[^"]+)"\)',
            line.strip(),
        )
        if match:
            registration = match.group(1)
            method = registration.removesuffix("_dynamic").upper()
            route_path = _normalize_gui_literal(match.group(2))
            if route_path == "/api/meetings/{meeting_id}/events":
                handler_form = "sse"
            else:
                handler_form = "prefix" if registration.endswith("_dynamic") else _handler_form(route_path)
            routes.add(Route(route_path, method, handler_form))
    return routes


def _parse_gui_routes(path: Path) -> set[Route]:
    # The if-chain in gui.py is being replaced by route-table modules (R2);
    # the inventory is the union of both registration styles.
    routes: set[Route] = set()
    route_modules = (
        *sorted(path.parent.glob("gui_*_http.py")),
        path.parent / "web" / "websocket.py",
        *sorted((path.parent / "web" / "routes").rglob("*.py")),
        *sorted((path.parent / "legacy" / "live_agent" / "http").glob("*.py")),
        *sorted((path.parent / "legacy" / "meeting" / "http").glob("*.py")),
        *sorted((path.parent / "legacy" / "diagnostics" / "http").glob("*.py")),
        *sorted((path.parent / "features").rglob("routes.py")),
    )
    for module_path in route_modules:
        routes |= _parse_router_module_routes(module_path)
    text = path.read_text(encoding="utf-8")
    current_method = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("def do_GET"):
            current_method = "GET"
            continue
        if stripped.startswith("def do_POST"):
            current_method = "POST"
            continue
        if stripped.startswith("def log_message"):
            current_method = ""
            continue
        if current_method not in {"GET", "POST"}:
            continue

        exact = re.search(r'(?:path|parsed\.path) == "(/api/[^"]+)"', stripped)
        if exact:
            route_path = _normalize_gui_literal(exact.group(1))
            routes.add(Route(route_path, current_method, _handler_form(route_path)))
            continue

        startswith = re.search(r'path\.startswith\("(/api/[^"]+)"\)', stripped)
        if startswith:
            prefix = startswith.group(1)
            if prefix == "/api/attachments/":
                routes.add(Route("/api/attachments/{attachment_id}", current_method, "prefix"))
            elif prefix == "/api/meetings/":
                # More specific meeting lifecycle and event routes are modeled
                # below from their guards/helper calls.
                routes.add(Route("/api/meetings/{meeting_id}", current_method, "prefix"))

        meeting_lifecycle = 'path.startswith("/api/meetings/") and path.endswith("/lifecycle")' in stripped
        if meeting_lifecycle:
            routes.add(Route("/api/meetings/{meeting_id}/lifecycle", current_method, "prefix"))

        meeting_workroom_queue = 'path.startswith("/api/meetings/") and path.endswith("/workroom-queue")' in stripped
        if meeting_workroom_queue:
            routes.add(Route("/api/meetings/{meeting_id}/workroom-queue", current_method, "prefix"))

        if "self._meeting_events_id(path)" in stripped:
            routes.add(Route("/api/meetings/{meeting_id}/events", current_method, "sse"))

        for action in _helper_actions(stripped, "_live_agent_action_path"):
            routes.add(Route(f"/api/live-agents/{{agent_id}}/{action}", current_method, "prefix"))
        for action in _helper_actions(stripped, "_meeting_live_agent_turn_action_path"):
            routes.add(Route(f"/api/meetings/{{meeting_id}}/live-agent-turns/{action}", current_method, "prefix"))
        for function_name, action in (
            ("_meeting_live_agent_turn_request_path", "request"),
            ("_meeting_live_agent_turn_call_path", "call"),
            ("_meeting_live_agent_turn_sequence_path", "sequence"),
            ("_meeting_live_agent_turn_rounds_path", "rounds"),
            ("_meeting_live_agent_turn_round_path", "round"),
            ("_meeting_live_agent_turn_preset_path", "preset"),
        ):
            if function_name in stripped:
                routes.add(Route(f"/api/meetings/{{meeting_id}}/live-agent-turns/{action}", current_method, "prefix"))
        if "_meeting_finalize_path" in stripped:
            routes.add(Route("/api/meetings/{meeting_id}/finalize", current_method, "prefix"))
        if "_meeting_review_checkpoint_path" in stripped:
            routes.add(Route("/api/meetings/{meeting_id}/review-checkpoints", current_method, "prefix"))
        for action in _helper_actions(stripped, "_live_agent_process_action_path"):
            routes.add(Route(f"/api/live-agent-processes/{{group_id}}/{action}", current_method, "prefix"))
    return routes


def _helper_actions(line: str, helper_name: str) -> list[str]:
    pattern = rf"{re.escape(helper_name)}\((?:path|parsed\.path), \"([^\"]+)\"\)"
    return re.findall(pattern, line)


def _handler_form(path: str) -> str:
    if path in {"/api/events/lobby", "/api/events/side-chat", "/api/events/roster", "/api/room-events/stream"}:
        return "sse"
    return "exact"


def _normalize_gui_literal(path: str) -> str:
    return path.rstrip("?")


def _parse_api_ts_routes(path: Path) -> set[Route]:
    routes: set[Route] = set()
    for matched_path, method, _wrapper, _owner in _api_ts_route_refs_with_owners(path):
        routes.add(_route_from_api_ts_ref(matched_path, method))
    return routes


def _parse_api_ts_wrappers_by_route(path: Path) -> dict[Route, set[str]]:
    wrappers_by_route: dict[Route, set[str]] = defaultdict(set)
    for matched_path, method, wrapper, _owner in _api_ts_route_refs_with_owners(path):
        wrappers_by_route[_route_from_api_ts_ref(matched_path, method)].add(wrapper)
    return dict(wrappers_by_route)


def _parse_api_ts_exported_functions(path: Path) -> set[str]:
    functions: set[str] = set()
    for module_path in _api_module_paths(path):
        text = module_path.read_text(encoding="utf-8")
        functions.update(re.findall(r"\bexport (?:async )?function ([A-Za-z0-9_]+)\(", text))
    return functions


def _route_from_api_ts_ref(path: str, method: str) -> Route:
    normalized = _normalize_api_ts_path(path)
    handler_form = "sse" if method == "GET_SSE" else "exact"
    if method == "GET_SSE":
        method = "GET"
    elif "{" in normalized:
        handler_form = "prefix"
    return Route(normalized, method, handler_form)


def _api_ts_route_refs(text: str) -> list[tuple[str, str, str]]:
    lines = text.splitlines()
    refs: list[tuple[str, str, str]] = []
    current_function = ""
    current_method = "GET"
    for line in lines:
        function_match = re.search(r"\bexport (?:async )?function ([A-Za-z0-9_]+)\(", line)
        if function_match:
            current_function = function_match.group(1)
            current_method = "GET"
        if current_function and "postJson" in line:
            current_method = "POST"
        elif current_function and "deleteJson" in line:
            current_method = "DELETE"
        elif current_function and "EventSource" in line:
            current_method = "GET_SSE"
        if 'method: "DELETE"' in line:
            current_method = "DELETE"
        for matched_path in re.findall(r'["`](/api/[^"`]+)["`]', line):
            refs.append((matched_path, current_method, current_function))
    return refs


def _api_ts_route_refs_with_owners(path: Path) -> list[tuple[str, str, str, Path]]:
    refs: list[tuple[str, str, str, Path]] = []
    for module_path in _api_module_paths(path):
        refs.extend((*ref, module_path) for ref in _api_ts_route_refs(module_path.read_text(encoding="utf-8")))
    return refs


def _parse_api_ts_route_owners(path: Path) -> dict[Route, Path]:
    owners: dict[Route, Path] = {}
    for matched_path, method, _wrapper, owner in _api_ts_route_refs_with_owners(path):
        owners[_route_from_api_ts_ref(matched_path, method)] = owner
    return owners


def _api_module_paths(entry_path: Path) -> list[Path]:
    """Follow only local export-star and named re-exports from the API barrel."""
    pending = [entry_path.resolve()]
    visited: set[Path] = set()
    modules: list[Path] = []
    while pending:
        module_path = pending.pop()
        if module_path in visited:
            continue
        visited.add(module_path)
        modules.append(module_path)
        text = module_path.read_text(encoding="utf-8")
        for specifier in re.findall(
            r"export\s+(?:\*|\{[^}]*\})\s+from\s+[\"'](\.[^\"']+)[\"']",
            text,
            flags=re.DOTALL,
        ):
            resolved = _resolve_api_module(module_path, specifier)
            if resolved is not None:
                pending.append(resolved)
    return sorted(modules)


def _resolve_api_module(module_path: Path, specifier: str) -> Path | None:
    base = (module_path.parent / specifier).resolve()
    candidates = [base, base.with_suffix(".ts"), base.with_suffix(".tsx"), base / "index.ts"]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _normalize_api_ts_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{queryString\(.*\)\}", "", path)
    path = re.sub(r"\$\{encodeURIComponent\(meetingId\)\}", "{meeting_id}", path)
    return path


def _parse_matrix_appendix(path: Path) -> list[MatrixRoute]:
    text = path.read_text(encoding="utf-8")
    heading = "## API/SSE Inventory Appendix"
    if heading not in text:
        raise AssertionError(f"{heading} is missing from {path}")
    section = text.split(heading, 1)[1]
    section = section.split("\n## ", 1)[0]
    entries: list[MatrixRoute] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("| Path ") or line.startswith("| ---"):
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) != 6:
            raise AssertionError(f"Inventory appendix row must have 6 columns: {raw_line}")
        path_value, method, handler_form, react_wrapper, react_wired, _notes = columns
        entries.append(
            MatrixRoute(
                Route(_strip_markdown_code(path_value), method, handler_form),
                _strip_markdown_code(react_wrapper),
                react_wired,
            )
        )
    if not entries:
        raise AssertionError(f"{heading} has no route rows")
    return entries


def _parse_surface_inventory_api_wrapper_names(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    section = text.split("## Surface Inventory", 1)[1].split("\n## ", 1)[0]
    wrappers: set[str] = set()
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("| Surface ") or line.startswith("| ---"):
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) < 3:
            continue
        react_equivalent = columns[2]
        for name in re.findall(r"`([a-z][A-Za-z0-9_]+)\(\)`", react_equivalent):
            if name.startswith(("fetch", "post", "upload", "start", "stop", "subscribe", "send", "cast", "resolve")):
                wrappers.add(name)
    return wrappers


def _wrapper_names(value: str) -> list[str]:
    if not value or value == "-":
        return []
    return [part.strip().rstrip("()") for part in value.split(",") if part.strip()]


def _strip_markdown_code(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value
