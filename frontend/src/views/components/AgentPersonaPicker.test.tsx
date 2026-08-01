import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AgentPersonaPicker from "./AgentPersonaPicker";

const personaApi = vi.hoisted(() => ({
  fetchPersonaAssets: vi.fn(),
  importPersonaAsset: vi.fn(),
}));

vi.mock("../../api/personas", () => ({
  fetchPersonaAssets: personaApi.fetchPersonaAssets,
  importPersonaAsset: personaApi.importPersonaAsset,
}));

afterEach(cleanup);

beforeEach(() => {
  personaApi.fetchPersonaAssets.mockReset();
  personaApi.importPersonaAsset.mockReset();
  personaApi.fetchPersonaAssets.mockResolvedValue([
    {
      id: "guide",
      display_name: "Night Guide",
      asset_kind: "card",
      lorebook_count: 2,
      asset_count: 1,
      ignored_feature_count: 0,
      tag_count: 0,
      thumbnail_url: "/api/personas/guide/thumbnail",
    },
    {
      id: "weather-module",
      display_name: "Weather Module",
      asset_kind: "module",
      lorebook_count: 4,
      asset_count: 0,
      ignored_feature_count: 3,
      tag_count: 0,
    },
  ]);
});

describe("AgentPersonaPicker", () => {
  it("distinguishes cards from modules and reports the applied selection", async () => {
    const onChange = vi.fn();
    render(
      <AgentPersonaPicker
        value="guide"
        applied={{
          id: "guide",
          display_name: "Night Guide",
          asset_kind: "card",
          lorebook_count: 2,
          asset_count: 1,
          ignored_feature_count: 0,
          tag_count: 0,
        }}
        onChange={onChange}
      />
    );

    expect(personaApi.fetchPersonaAssets).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Night Guide/ })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Night Guide/ }));
    await waitFor(() => expect(screen.getByText("Weather Module")).toBeTruthy());
    expect(screen.getByRole("radio", { name: /Night Guide/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: /적용 안 함/ }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText("적용됨")).toBeTruthy();
    expect(screen.getByText(/Risu 모듈 · 로어 4/)).toBeTruthy();

    await userEvent.click(screen.getByRole("radio", { name: /Weather Module/ }));
    expect(onChange).toHaveBeenCalledWith("weather-module");
  });

  it("selects a newly imported supported file", async () => {
    const onChange = vi.fn();
    personaApi.importPersonaAsset.mockResolvedValue({
      id: "new-module",
      display_name: "New Module",
      asset_kind: "module",
      lorebook_count: 1,
      asset_count: 0,
      ignored_feature_count: 0,
      tag_count: 0,
    });
    render(<AgentPersonaPicker value="" onChange={onChange} />);

    const input = screen.getByLabelText("파일 가져오기").querySelector("input") ||
      screen.getByLabelText("파일 가져오기");
    await userEvent.upload(input as HTMLInputElement, new File(["module"], "module.risum"));

    await waitFor(() => expect(onChange).toHaveBeenCalledWith("new-module"));
    expect(screen.getByText(/New Module 가져오기 완료/)).toBeTruthy();
  });

  it("bounds a large library and finds a card through search", async () => {
    const onChange = vi.fn();
    personaApi.fetchPersonaAssets.mockResolvedValue(
      Array.from({ length: 100 }, (_, index) => ({
        id: `persona-${index}`,
        display_name: `Persona ${index}`,
        asset_kind: "card",
        lorebook_count: 0,
        asset_count: 0,
        ignored_feature_count: 0,
        tag_count: 0,
      }))
    );
    render(<AgentPersonaPicker value="" onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: /적용 안 함/ }));
    await waitFor(() => expect(screen.getByText("Persona 0")).toBeTruthy());
    // Nothing is applied yet, so the list is the 8 shown cards; a "clear"
    // row would only repeat what the closed trigger already says.
    expect(screen.getAllByRole("radio")).toHaveLength(8);
    expect(screen.queryByText("Persona 99")).toBeNull();

    await userEvent.type(screen.getByPlaceholderText("봇카드 또는 모듈 검색"), "Persona 99");
    await userEvent.click(screen.getByRole("radio", { name: /Persona 99/ }));
    expect(onChange).toHaveBeenCalledWith("persona-99");
  });
});
