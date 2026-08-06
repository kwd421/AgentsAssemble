import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LobbyAttachments from "./LobbyAttachments";

describe("LobbyAttachments", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["private image"], { type: "image/png" }),
    }));
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:authorized-preview"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads a private room attachment with the current room session before rendering it", async () => {
    render(
      <LobbyAttachments
        sessionToken="current-room-session"
        attachments={[
          {
            id: "attachment-1",
            filename: "private.png",
            content_type: "image/png",
            size: 13,
            is_image: true,
            url: "/api/attachments/attachment-1?view=1",
            download_url: "/api/attachments/attachment-1?download=1",
          },
        ]}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("img", { name: "private.png" }).getAttribute("src")).toBe(
        "blob:authorized-preview"
      );
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/attachments/attachment-1?view=1",
      expect.objectContaining({
        headers: { Authorization: "Bearer current-room-session" },
      })
    );
  });
});
