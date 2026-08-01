import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProviderControlSelect from "./ProviderControlSelect";

afterEach(cleanup);

describe("ProviderControlSelect", () => {
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
});
