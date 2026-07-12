import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class FrontendApiRuntimeHelpersTests(unittest.TestCase):
    def test_compiler_rewrites_explicit_tsx_and_directory_imports(self):
        project_root = Path(__file__).resolve().parents[1]
        helper_uri = (project_root / "tests" / "frontend_api_runtime_helpers.mjs").as_uri()

        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_root = Path(temp_dir) / "fixture"
            output_root = Path(temp_dir) / "compiled"
            package_root = fixture_root / "package"
            package_root.mkdir(parents=True)
            (fixture_root / "component.tsx").write_text(
                'export const componentValue = "tsx";\n',
                encoding="utf-8",
            )
            (package_root / "index.ts").write_text(
                'export const packageValue = "index";\n',
                encoding="utf-8",
            )
            entry_path = fixture_root / "entry.ts"
            entry_path.write_text(
                textwrap.dedent(
                    """
                    import { componentValue } from "./component.tsx";
                    import { packageValue } from "./package";
                    export const result = `${componentValue}:${packageValue}`;
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            script = textwrap.dedent(
                f"""
                import {{ pathToFileURL }} from "node:url";
                import {{ compileTypeScriptModule }} from {json.dumps(helper_uri)};
                const modulePath = await compileTypeScriptModule(
                  {json.dumps(str(entry_path))},
                  {json.dumps(str(output_root))},
                );
                const compiled = await import(pathToFileURL(modulePath));
                console.log(JSON.stringify({{ result: compiled.result }}));
                """
            )
            completed = subprocess.run(
                ["node", "--input-type=module", "--eval", script],
                cwd=project_root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"result": "tsx:index"})


if __name__ == "__main__":
    unittest.main()
