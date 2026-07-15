import ast
import unittest
from collections import defaultdict
from pathlib import Path

from agentsassemble.gui_router import match_route_template
from agentsassemble.gui_static_transport import REACT_APP_EXACT_PATHS


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCE = REPOSITORY_ROOT / "agentsassemble" / "gui.py"
GUI_ROUTE_MODULES = tuple(sorted((REPOSITORY_ROOT / "agentsassemble").glob("gui*_http.py")))
DYNAMIC_ROUTE_HELPERS = {
    "_live_agent_process_action_path": ("POST", "/api/live-agent-processes/{group_id}/{action}"),
}
EXPECTED_DYNAMIC_ROUTES = {
    ("GET", "/api/attachments/{attachment_id}"),
    ("GET", "/api/meetings/{meeting_id}"),
    ("GET", "/api/meetings/{meeting_id}/events"),
    ("GET", "/api/meetings/{meeting_id}/lifecycle"),
    ("GET", "/api/meetings/{meeting_id}/workroom-queue"),
    ("GET", "/api/live-agents/{agent_id}/return-packet"),
    ("GET", "/api/live-agents/{agent_id}/room"),
    ("POST", "/api/meetings/{meeting_id}/finalize"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/call"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/preset"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/request"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/round"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/rounds"),
    ("POST", "/api/meetings/{meeting_id}/live-agent-turns/sequence"),
    ("POST", "/api/meetings/{meeting_id}/review-checkpoints"),
    ("POST", "/api/live-agents/{agent_id}/engagement"),
    ("POST", "/api/live-agents/{agent_id}/heartbeat"),
    ("POST", "/api/live-agents/{agent_id}/leave"),
    ("POST", "/api/live-agents/{agent_id}/lobby"),
    ("POST", "/api/live-agents/{agent_id}/official-turn"),
    ("POST", "/api/live-agents/{agent_id}/probe"),
    ("POST", "/api/live-agents/{agent_id}/dm-reply"),
    ("POST", "/api/live-agent-processes/{group_id}/stop"),
    ("POST", "/api/live-agent-processes/{group_id}/restart"),
    ("POST", "/api/live-agent-processes/{group_id}/recover"),
    ("POST", "/api/live-agent-session-runs/{run_id}/pause"),
    ("POST", "/api/live-agent-session-runs/{run_id}/resume"),
    ("POST", "/api/live-agent-session-runs/{run_id}/stop"),
    ("POST", "/api/live-agent-session-runs/{run_id}/retry-now"),
}

EXPECTED_RETAINED_HANDLER_EXACT_ROUTES = {
    ("GET", "/ws"),
}

EXPECTED_RETIRED_EXACT_ROUTES = {
    ("GET", "/api/codex-sessions"),
    ("GET", "/api/live-agent-create/options"),
    ("GET", "/api/provider-sessions"),
    ("POST", "/api/demo"),
    ("POST", "/api/live-agent-create"),
    ("POST", "/api/live-agent-create/check"),
    ("POST", "/api/live-agent-room/expel"),
}


def _registered_exact_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for source_path in GUI_ROUTE_MODULES:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"get", "post", "delete"} or not node.args:
                continue
            path_node = node.args[0]
            if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
                routes.add((node.func.attr.upper(), path_node.value))
    return routes


def _legacy_exact_routes() -> set[tuple[str, str]]:
    tree = ast.parse(GUI_SOURCE.read_text(encoding="utf-8"))
    routes: set[tuple[str, str]] = set()

    class LegacyRouteVisitor(ast.NodeVisitor):
        method = ""

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            previous_method = self.method
            if node.name in {"do_GET", "do_POST", "do_DELETE"}:
                self.method = node.name.removeprefix("do_")
            self.generic_visit(node)
            self.method = previous_method

        def visit_Compare(self, node: ast.Compare) -> None:
            if not self.method or len(node.ops) != 1:
                self.generic_visit(node)
                return
            operator = node.ops[0]
            if isinstance(operator, ast.Eq):
                operands = [node.left, *node.comparators]
                if any(_operand_name(operand) in {"path", "parsed.path", "self.path"} for operand in operands):
                    for operand in operands:
                        routes.update((self.method, path) for path in _literal_paths(operand))
            elif isinstance(operator, ast.In) and _operand_name(node.left) in {"path", "parsed.path", "self.path"}:
                routes.update((self.method, path) for path in _literal_paths(node.comparators[0]))
            self.generic_visit(node)

    LegacyRouteVisitor().visit(tree)
    return routes


def _operand_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


def _literal_paths(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/"):
        return {node.value}
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return {
            value.value
            for value in node.elts
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.startswith("/")
        }
    return set()


def _dynamic_route_owners() -> dict[tuple[str, str], set[Path]]:
    owners: dict[tuple[str, str], set[Path]] = defaultdict(set)
    for source_path in (GUI_SOURCE, *GUI_ROUTE_MODULES):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        class DynamicRouteVisitor(ast.NodeVisitor):
            method = ""

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                previous_method = self.method
                if node.name in {"do_GET", "do_POST", "do_DELETE"}:
                    self.method = node.name.removeprefix("do_")
                self.generic_visit(node)
                self.method = previous_method

            def visit_Call(self, node: ast.Call) -> None:
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"get_dynamic", "post_dynamic"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    method = node.func.attr.removesuffix("_dynamic").upper()
                    owners[(method, node.args[0].value)].add(source_path)
                helper_name = node.func.id if isinstance(node.func, ast.Name) else ""
                route_family = DYNAMIC_ROUTE_HELPERS.get(helper_name)
                if route_family and len(node.args) >= 2:
                    default_method, template = route_family
                    action_node = node.args[1]
                    if isinstance(action_node, ast.Constant) and isinstance(action_node.value, str):
                        method = self.method or default_method
                        owners[(method, template.replace("{action}", action_node.value))].add(source_path)
                self.generic_visit(node)

        DynamicRouteVisitor().visit(tree)
    return dict(owners)


class GuiRouteOwnershipTests(unittest.TestCase):
    def test_registered_routes_do_not_shadow_retained_legacy_exact_routes(self) -> None:
        overlap = _registered_exact_routes().intersection(_legacy_exact_routes())

        self.assertEqual(
            overlap,
            set(),
            "Move an exact route to the Router or retain it in the legacy chain, never both.",
        )

    def test_static_transport_owns_the_react_bootstrap_routes(self) -> None:
        self.assertIn("/join", REACT_APP_EXACT_PATHS)
        self.assertIn("/app", REACT_APP_EXACT_PATHS)

    def test_handler_exact_routes_are_limited_to_transport_and_static_delivery(self) -> None:
        self.assertEqual(_legacy_exact_routes(), EXPECTED_RETAINED_HANDLER_EXACT_ROUTES)

    def test_retired_exact_routes_are_router_owned_tombstones(self) -> None:
        self.assertTrue(EXPECTED_RETIRED_EXACT_ROUTES.issubset(_registered_exact_routes()))

    def test_dynamic_route_inventory_is_explicit_and_has_one_owner(self) -> None:
        owners = _dynamic_route_owners()

        self.assertEqual(set(owners), EXPECTED_DYNAMIC_ROUTES)
        self.assertTrue(all(len(route_owners) == 1 for route_owners in owners.values()))

    def test_dynamic_route_matcher_rejects_unsafe_segments_and_false_prefixes(self) -> None:
        template = "/api/live-agent-processes/{group_id}/stop"

        self.assertEqual(
            match_route_template(template, "/api/live-agent-processes/group-one/stop"),
            {"group_id": "group-one"},
        )
        for value in ("group%2Fone", "group%5Cone", "%2e", "%2e%2e", "%00", "a" * 257):
            with self.subTest(value=value):
                self.assertIsNone(match_route_template(template, f"/api/live-agent-processes/{value}/stop"))
        self.assertIsNone(match_route_template(template, "/api/live-agent-processes/group/stop/extra"))
        self.assertIsNone(match_route_template(template, "/api/not-live-agent-processes/group/stop"))


if __name__ == "__main__":
    unittest.main()
