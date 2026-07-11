import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import DiscordText from "./DiscordText";

describe("DiscordText", () => {
  it("renders GitHub-flavored markdown tables", () => {
    const { container } = render(
      <DiscordText text={"| 이름 | 상태 |\n| --- | --- |\n| Codex | 대기 |"} />
    );

    expect(container.querySelector("table")).not.toBeNull();
    expect(screen.getByRole("columnheader", { name: "이름" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "대기" })).toBeTruthy();
  });

  it("preserves room mentions while blocking raw html", () => {
    const { container } = render(<DiscordText text={"@codex **확인** <script>alert(1)</script>"} />);

    expect(container.querySelector(".dc-mention")?.textContent).toBe("@codex");
    expect(container.querySelector("strong")?.textContent).toBe("확인");
    expect(container.querySelector("script")).toBeNull();
  });
});
