import ast
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUI_SOURCE = REPOSITORY_ROOT / "agentsassemble" / "gui.py"
GUI_ROUTE_MODULES = tuple(sorted((REPOSITORY_ROOT / "agentsassemble").glob("gui*_http.py")))


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
            if self.method and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                operands = [node.left, *node.comparators]
                names = {_operand_name(operand) for operand in operands}
                if names.intersection({"path", "parsed.path", "self.path"}):
                    for operand in operands:
                        if (
                            isinstance(operand, ast.Constant)
                            and isinstance(operand.value, str)
                            and operand.value.startswith("/")
                        ):
                            routes.add((self.method, operand.value))
            self.generic_visit(node)

    LegacyRouteVisitor().visit(tree)
    return routes


def _operand_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


class GuiRouteOwnershipTests(unittest.TestCase):
    def test_registered_routes_do_not_shadow_retained_legacy_exact_routes(self) -> None:
        overlap = _registered_exact_routes().intersection(_legacy_exact_routes())

        self.assertEqual(
            overlap,
            set(),
            "Move an exact route to the Router or retain it in the legacy chain, never both.",
        )


if __name__ == "__main__":
    unittest.main()
