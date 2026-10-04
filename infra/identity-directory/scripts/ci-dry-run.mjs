// Validate the Worker configuration/bundle without the separately owned Rust
// frontend. Production deployments must still supply its matching assets.
import { mkdtempSync, readFileSync, rmSync, writeFileSync, unlinkSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const config = new URL(`../.wrangler-ci-${process.pid}.toml`, import.meta.url);
let assets = false;
const source = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
const workerOnly = source.split("\n").filter(line => {
  if (line.trim().startsWith("[")) assets = line.trim() === "[assets]";
  return !assets;
}).join("\n");
// Keep the config beside the original so relative main/migration paths agree.
writeFileSync(config, workerOnly, { flag: "wx" });
const outdir = mkdtempSync(join(tmpdir(), "agentsassemble-identity-ci-"));
try {
  const result = spawnSync("npx", ["--yes", "wrangler@4", "deploy", "--dry-run",
    "--config", fileURLToPath(config), "--outdir", outdir],
  { cwd: root, stdio: "inherit", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  unlinkSync(config);
  rmSync(outdir, { recursive: true, force: true });
}
