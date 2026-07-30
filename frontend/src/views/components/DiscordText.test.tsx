import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DiscordText from "./DiscordText";

describe("DiscordText", () => {
  it("preserves structured provider replies as GitHub-flavored markdown", () => {
    const { container } = render(
      <DiscordText
        text={[
          "First paragraph.",
          "",
          "Second paragraph.",
          "",
          "| Item | Status | Note |",
          "| --- | --- | --- |",
          "| Table | OK | Three columns |",
          "",
          "- first item",
          "- second item",
          "",
          "Inline `ok`.",
        ].join("\n")}
      />
    );

    expect(container.querySelectorAll("p")).toHaveLength(3);
    expect(container.querySelector("table")).not.toBeNull();
    expect(screen.getByRole("columnheader", { name: "Item" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "Three columns" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("code")?.textContent).toBe("ok");
  });

  it("preserves room mentions while blocking raw html", () => {
    const { container } = render(<DiscordText text={"@codex **확인** <script>alert(1)</script>"} />);

    expect(container.querySelector(".dc-mention")?.textContent).toBe("@codex");
    expect(container.querySelector("strong")?.textContent).toBe("확인");
    expect(container.querySelector("script")).toBeNull();
  });

  it("keeps numeric ranges with single tildes while rendering explicit strikethrough", () => {
    const { container } = render(
      <DiscordText text={"game.py:385~393, routes.py:63~67·84~88, ~~removed~~"} />
    );

    expect(container.textContent).toContain("game.py:385~393");
    expect(container.textContent).toContain("routes.py:63~67·84~88");
    expect(container.querySelectorAll("del")).toHaveLength(1);
    expect(container.querySelector("del")?.textContent).toBe("removed");
  });
});
