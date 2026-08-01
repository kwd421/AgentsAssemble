/**
 * Insert the delimiter row that GFM tables require but models often omit.
 *
 * A pipe table is only a table in GFM if the header is followed by a row of
 * dashes. DeepSeek (and it is not alone) writes the header and then goes
 * straight to data, so remark-gfm sees ordinary paragraphs and the reader gets
 * a wall of pipes. The message itself is fine -- newlines survive publication,
 * the renderer has remark-gfm on, and the table CSS is complete -- so the
 * repair belongs at render time, right before parsing, and never touches what
 * was stored.
 */

const MIN_TABLE_COLUMNS = 2;
const MIN_TABLE_ROWS = 2;

function isPipeRow(line: string): boolean {
  if (/^(?: {4}|\t)/.test(line)) return false;
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 2;
}

function fenceMarker(line: string): string {
  return line.match(/^\s{0,3}(`{3,}|~{3,})/)?.[1] || "";
}

function closesFence(line: string, activeFence: string): boolean {
  const marker = line.match(/^\s{0,3}(`{3,}|~{3,})\s*$/)?.[1] || "";
  return (
    Boolean(marker) &&
    marker[0] === activeFence[0] &&
    marker.length >= activeFence.length
  );
}

function isDelimiterRow(line: string): boolean {
  return /^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$/.test(line);
}

function cellCount(line: string): number {
  // A leading and trailing pipe produce empty edge segments; drop them.
  return line.trim().slice(1, -1).split("|").length;
}

function delimiterRowFor(columns: number): string {
  return `|${Array.from({ length: columns }, () => " --- ").join("|")}|`;
}

export function repairMarkdownTables(text: string): string {
  const source = String(text ?? "");
  if (!source.includes("|")) return source;

  const lines = source.split("\n");
  const output: string[] = [];
  let index = 0;
  let activeFence = "";

  while (index < lines.length) {
    const line = lines[index];
    const marker = fenceMarker(line);
    if (activeFence) {
      output.push(line);
      if (closesFence(line, activeFence)) {
        activeFence = "";
      }
      index += 1;
      continue;
    }
    if (marker) {
      activeFence = marker;
      output.push(line);
      index += 1;
      continue;
    }
    if (!isPipeRow(line)) {
      output.push(line);
      index += 1;
      continue;
    }

    // Collect the whole run of pipe rows so a table is judged as a block.
    let end = index;
    while (end < lines.length && isPipeRow(lines[end])) end += 1;
    const block = lines.slice(index, end);
    const columns = cellCount(block[0]);
    const alreadyValid = block.length > 1 && isDelimiterRow(block[1]);
    const uniformColumns = block.every((row) => cellCount(row) === columns);

    if (
      alreadyValid ||
      block.length < MIN_TABLE_ROWS ||
      columns < MIN_TABLE_COLUMNS ||
      !uniformColumns
    ) {
      output.push(...block);
    } else {
      output.push(block[0], delimiterRowFor(columns), ...block.slice(1));
    }
    index = end;
  }

  return output.join("\n");
}
