import fs from "node:fs/promises";
import path from "node:path";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";

async function existingTypeScriptImport(sourcePath, specifier) {
  const basePath = path.resolve(path.dirname(sourcePath), specifier);
  const candidates = [
    basePath,
    `${basePath}.ts`,
    `${basePath}.tsx`,
    path.join(basePath, "index.ts"),
    path.join(basePath, "index.tsx"),
  ];
  for (const candidate of candidates) {
    try {
      const stat = await fs.stat(candidate);
      if (stat.isFile()) return candidate;
    } catch {
      // Try the next TypeScript resolution candidate.
    }
  }
  return null;
}

function outputPathFor(sourcePath, outputRoot) {
  const normalizedPath = path.resolve(sourcePath);
  const filesystemRoot = path.parse(normalizedPath).root;
  const containedPath = path.relative(filesystemRoot, normalizedPath);
  return path.join(outputRoot, containedPath).replace(/\.(tsx?|mts|cts)$/, ".mjs");
}

async function rewriteLocalImportSpecifiers(outputText, sourcePath, outputRoot) {
  const pattern = /(from\s+["']|import\(\s*["'])(\.[^"']+)(["'])/g;
  const replacements = new Map();

  for (const match of outputText.matchAll(pattern)) {
    const specifier = match[2];
    if (replacements.has(specifier)) continue;
    const dependencyPath = await existingTypeScriptImport(sourcePath, specifier);
    if (!dependencyPath) continue;
    const dependencyOutput = outputPathFor(dependencyPath, outputRoot);
    const relativePath = path.relative(path.dirname(outputPathFor(sourcePath, outputRoot)), dependencyOutput);
    const relativeOutput = (
      relativePath.startsWith(".") ? relativePath : `.${path.sep}${relativePath}`
    ).replaceAll(path.sep, "/");
    replacements.set(specifier, relativeOutput);
  }

  return outputText.replace(pattern, (match, prefix, specifier, suffix) => {
    const replacement = replacements.get(specifier);
    return replacement ? `${prefix}${replacement}${suffix}` : match;
  });
}

export async function compileTypeScriptModule(entryPath, outputRoot) {
  const compiled = new Set();

  async function compile(sourcePath) {
    const normalizedPath = path.resolve(sourcePath);
    if (compiled.has(normalizedPath)) return;
    compiled.add(normalizedPath);

    const source = await fs.readFile(normalizedPath, "utf8");
    const output = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
        importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
      },
      fileName: normalizedPath,
    }).outputText;
    const outputPath = outputPathFor(normalizedPath, outputRoot);
    await fs.mkdir(path.dirname(outputPath), { recursive: true });
    const rewrittenOutput = await rewriteLocalImportSpecifiers(output, normalizedPath, outputRoot);
    await fs.writeFile(outputPath, rewrittenOutput, "utf8");

    const imports = [...source.matchAll(/(?:from\s+["']|import\(\s*["'])(\.[^"']+)["']/g)];
    for (const [, specifier] of imports) {
      const dependencyPath = await existingTypeScriptImport(normalizedPath, specifier);
      if (dependencyPath) await compile(dependencyPath);
    }
  }

  await compile(entryPath);
  return outputPathFor(path.resolve(entryPath), outputRoot);
}
