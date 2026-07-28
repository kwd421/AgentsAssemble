import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ChannelHeader from "./ChannelHeader";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderHeader({ mobile }: { mobile: boolean }) {
  const onToggleMembers = vi.fn();
  const onOpenMobileInfo = vi.fn();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: mobile })
  );
  render(
    <ChannelHeader
      icon="#"
      title="general"
      membersOpen={false}
      onToggleMembers={onToggleMembers}
      onOpenMobileInfo={onOpenMobileInfo}
    />
  );
  return { onToggleMembers, onOpenMobileInfo };
}

describe("ChannelHeader member access", () => {
  it("opens the mobile room information panel at the mobile breakpoint", () => {
    const handlers = renderHeader({ mobile: true });

    fireEvent.click(screen.getByRole("button", { name: "멤버 목록 토글" }));

    expect(handlers.onOpenMobileInfo).toHaveBeenCalledOnce();
    expect(handlers.onToggleMembers).not.toHaveBeenCalled();
  });

  it("toggles the full member panel above the mobile breakpoint", () => {
    const handlers = renderHeader({ mobile: false });

    fireEvent.click(screen.getByRole("button", { name: "멤버 목록 토글" }));

    expect(handlers.onToggleMembers).toHaveBeenCalledOnce();
    expect(handlers.onOpenMobileInfo).not.toHaveBeenCalled();
  });
});
