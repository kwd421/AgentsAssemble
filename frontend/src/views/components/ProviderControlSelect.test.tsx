import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProviderControlSelect from "./ProviderControlSelect";

afterEach(cleanup);

describe("ProviderControlSelect", () => {
  it("drills into model families inside the same bounded menu", async () => {
    render(
      <ProviderControlSelect
        label="모델"
        options={[
          { value: "gemini/flash", label: "Gemini Flash", metadata: { family: "Gemini" } },
          { value: "gemini/pro", label: "Gemini Pro", metadata: { family: "Gemini" } },
          { value: "claude/sonnet", label: "Claude Sonnet", metadata: { family: "Claude" } },
          { value: "claude/opus", label: "Claude Opus", metadata: { family: "Claude" } },
        ]}
        value="gemini/flash"
        onChange={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    expect(screen.queryByText("모델 카탈로그")).toBeNull();
    expect(screen.queryByText("제공사 또는 모델 선택")).toBeNull();
    await userEvent.click(
      screen.getByRole("menuitem", { name: "Gemini 제공사, 2개 모델" })
    );

    expect(screen.queryByRole("menu", { name: "모델 분류" })).toBeNull();
    expect(screen.getAllByRole("listbox")).toHaveLength(1);
    expect(screen.getByRole("option", { name: "Gemini Flash" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Claude Sonnet" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "모델 목록으로 돌아가기" }));
    expect(screen.getByRole("menu", { name: "모델 분류" })).toBeTruthy();
  });

  it("searches a large model catalog without traversing family menus", async () => {
    const onChange = vi.fn();
    const options = Array.from({ length: 100 }, (_, index) => ({
      value: `vendor/model-${index}`,
      label: `Model ${index}`,
      metadata: {
        family: index % 2 ? "Odd" : "Even",
        pricing: index % 10 === 0 ? "free" : "paid",
      },
    }));
    render(
      <ProviderControlSelect
        label="모델"
        options={options}
        value="vendor/model-0"
        onChange={onChange}
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "모델 검색" }), "Model 99");
    const results = screen.getByRole("listbox", { name: "모델" });
    expect(within(results).queryByRole("option", { name: "Model 0 Free" })).toBeNull();
    await userEvent.click(within(results).getByRole("option", { name: "Model 99" }));

    expect(onChange).toHaveBeenCalledWith("vendor/model-99");
  });

  it("filters models only when catalog metadata confirms free pricing", async () => {
    const onChange = vi.fn();
    render(
      <ProviderControlSelect
        label="모델"
        options={[
          { value: "vendor/free", label: "Free Model", metadata: { pricing: "free" } },
          { value: "vendor/tier", label: "Free Tier Model", metadata: { pricing: "free_tier" } },
          { value: "vendor/paid", label: "Paid Model" },
        ]}
        value="vendor/paid"
        onChange={onChange}
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    await userEvent.click(screen.getByRole("button", { name: "무료 모델만 보기" }));
    const results = screen.getByRole("listbox", { name: "모델" });
    expect(within(results).getByRole("option", { name: "Free Model Free" })).toBeTruthy();
    expect(within(results).getByRole("option", { name: "Free Tier Model Free tier" })).toBeTruthy();
    expect(within(results).queryByRole("option", { name: "Paid Model" })).toBeNull();
  });

  it("filters the catalog by capabilities reported by the provider", async () => {
    render(
      <ProviderControlSelect
        label="모델"
        options={[
          {
            value: "vendor/vision-reasoning",
            label: "Vision Reasoning",
            metadata: { vision: true, reasoning: true },
          },
          { value: "vendor/text", label: "Text Only", metadata: {} },
        ]}
        value="vendor/text"
        onChange={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    await userEvent.click(screen.getByRole("button", { name: "비전 모델만 보기" }));
    const results = screen.getByRole("listbox", { name: "모델" });
    expect(within(results).getByRole("option", { name: /Vision Reasoning/ })).toBeTruthy();
    expect(within(results).queryByRole("option", { name: "Text Only" })).toBeNull();
  });

  it("shows verified model limits and capabilities beside the hovered model", async () => {
    render(
      <ProviderControlSelect
        label="모델"
        options={[
          {
            value: "vendor/reasoner",
            label: "Reasoner",
            metadata: {
              context_length: 128_000,
              max_output_tokens: 16_384,
              input_price_per_million: "0.55",
              output_price_per_million: "2.19",
              reasoning: true,
              vision: false,
              training_policy: "사용될 수 있음 · opt-out 가능",
            },
          },
          { value: "vendor/other", label: "Other" },
        ]}
        value="vendor/other"
        onChange={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    expect(screen.queryByText("Reasoning")).toBeNull();
    await userEvent.hover(screen.getByRole("option", { name: /Reasoner/ }));

    const detail = document.querySelector(".dc-agent-model-details-popover");
    expect(detail).toBeTruthy();
    expect(within(detail as HTMLElement).getByText("128,000 tokens")).toBeTruthy();
    expect(within(detail as HTMLElement).getByText("16,384 tokens")).toBeTruthy();
    expect(within(detail as HTMLElement).getByText("$0.55/M")).toBeTruthy();
    expect(within(detail as HTMLElement).getByText("$2.19/M")).toBeTruthy();
    expect(within(detail as HTMLElement).getAllByText("지원")).toHaveLength(1);
    expect(within(detail as HTMLElement).getAllByText("미지원")).toHaveLength(1);
    expect(within(detail as HTMLElement).getByText("사용될 수 있음 · opt-out 가능")).toBeTruthy();
  });
});

describe("whole-row menu sizing", () => {
  function renderWithRowHeight(height: number, count = 30) {
    const original = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function () {
      const rect = original.call(this) as DOMRect;
      if (this.tagName === "BUTTON" && this.closest(".dc-agent-select-menu")) {
        return { ...rect, height } as DOMRect;
      }
      return rect;
    };
    const cleanupRect = () => {
      HTMLElement.prototype.getBoundingClientRect = original;
    };
    const options = Array.from({ length: count }, (_, index) => ({
      value: `m-${index}`,
      label: `Model ${index}`,
    }));
    render(
      <ProviderControlSelect label="모델" options={options} value="" onChange={vi.fn()} />
    );
    return cleanupRect;
  }

  it("sets the row height from a real row so the last one is not sliced", async () => {
    // A flat pixel cap left 6.44 rows visible and the 7th half-drawn, which read
    // as clipped rather than scrollable.
    const cleanupRect = renderWithRowHeight(34);
    try {
      await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
      const menu = document.querySelector(".dc-agent-select-menu") as HTMLElement;
      expect(menu.style.getPropertyValue("--dc-select-row")).toBe("34px");
    } finally {
      cleanupRect();
    }
  });

  it("uses the taller measurement when rows carry descriptions", async () => {
    const cleanupRect = renderWithRowHeight(50);
    try {
      await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
      const menu = document.querySelector(".dc-agent-select-menu") as HTMLElement;
      expect(menu.style.getPropertyValue("--dc-select-row")).toBe("50px");
    } finally {
      cleanupRect();
    }
  });

  it("re-measures when the observed menu changes size", async () => {
    // jsdom has no ResizeObserver, so stand one in and fire it the way a real
    // browser would when the filter shortens the list.
    const callbacks: Array<() => void> = [];
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      constructor(callback: () => void) {
        callbacks.push(callback);
      }
      observe() {}
      disconnect() {}
    };
    const cleanupRect = renderWithRowHeight(34);
    try {
      await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
      const menu = document.querySelector(".dc-agent-select-menu") as HTMLElement;
      expect(menu.style.getPropertyValue("--dc-select-row")).toBe("34px");

      menu.style.setProperty("--dc-select-row", "999px");
      callbacks.forEach((callback) => callback());

      expect(menu.style.getPropertyValue("--dc-select-row")).toBe("34px");
    } finally {
      cleanupRect();
      delete (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver;
    }
  });
});
