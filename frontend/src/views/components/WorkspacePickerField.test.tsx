import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkspacePickerField from "./WorkspacePickerField";

const apiMocks = vi.hoisted(() => ({
  chooseLocalWorkspace: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    chooseLocalWorkspace: apiMocks.chooseLocalWorkspace,
  };
});

afterEach(() => {
  cleanup();
  apiMocks.chooseLocalWorkspace.mockReset();
});

describe("WorkspacePickerField", () => {
  it("recovers from a failed native picker with a useful retry message", async () => {
    apiMocks.chooseLocalWorkspace.mockRejectedValue(
      new Error("workspace_picker_failed")
    );
    const onError = vi.fn();
    render(
      <WorkspacePickerField
        value=""
        onChange={vi.fn()}
        onError={onError}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "폴더 선택" }));

    await waitFor(() => expect(onError).toHaveBeenCalledOnce());
    expect(String(onError.mock.lastCall?.[0])).not.toContain("workspace_picker_");
    expect(
      (screen.getByRole("button", { name: "폴더 선택" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });
});
