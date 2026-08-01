import { describe, expect, it } from "vitest";
import { repairMarkdownTables } from "./repairMarkdownTables";

describe("repairMarkdownTables", () => {
  it("adds the delimiter row a model omitted", () => {
    // Exactly what DeepSeek published in room-20260731T000600: header, then
    // straight to data.
    const repaired = repairMarkdownTables(
      ["| # | 티커 | 현재가 |", "| 1 | AMZN | $270.77 |", "| 2 | AAPL | $300.01 |"].join("\n")
    );

    expect(repaired.split("\n")[1]).toBe("| --- | --- | --- |");
    expect(repaired.split("\n")).toHaveLength(4);
  });

  it("leaves a well-formed table untouched", () => {
    const source = ["| a | b |", "|---:|:--|", "| 1 | 2 |"].join("\n");

    expect(repairMarkdownTables(source)).toBe(source);
  });

  it("does not invent a table from ragged pipe lines", () => {
    // Prose that happens to contain pipes must not become a table.
    const source = ["| one |", "| two | three | four |"].join("\n");

    expect(repairMarkdownTables(source)).toBe(source);
  });

  it("ignores a lone pipe line", () => {
    const source = "| 그냥 한 줄 |";

    expect(repairMarkdownTables(source)).toBe(source);
  });

  it("repairs several tables in one message and keeps surrounding prose", () => {
    const repaired = repairMarkdownTables(
      [
        "【① 주수】",
        "| # | 티커 |",
        "| 1 | AMZN |",
        "",
        "【② 대금】",
        "| # | 티커 |",
        "| 1 | MSFT |",
      ].join("\n")
    );

    expect(repaired.split("\n").filter((line) => line === "| --- | --- |")).toHaveLength(2);
    expect(repaired).toContain("【① 주수】");
    expect(repaired).toContain("【② 대금】");
  });

  it("passes text with no pipes straight through", () => {
    expect(repairMarkdownTables("표 없는 평범한 문장")).toBe("표 없는 평범한 문장");
  });

  it("does not rewrite pipe-shaped examples inside fenced or indented code", () => {
    const source = [
      "```text",
      "| header | value |",
      "| first | second |",
      "```",
      "",
      "    | indented | example |",
      "    | first | second |",
    ].join("\n");

    expect(repairMarkdownTables(source)).toBe(source);
  });

  it("does not close a fence when the marker has trailing content", () => {
    const source = [
      "```text",
      "```not-a-closing-fence",
      "| header | value |",
      "| first | second |",
      "```",
    ].join("\n");

    expect(repairMarkdownTables(source)).toBe(source);
  });
});
