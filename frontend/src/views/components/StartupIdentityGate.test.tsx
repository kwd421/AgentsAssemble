import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchAccountStatus } from "../../api/identity";
import { saveUserProfile } from "../../api/room";
import {
  rememberGuestProfile,
  rememberStartupIdentitySelection,
} from "../../lib/deviceIdentity";
import StartupIdentityGate from "./StartupIdentityGate";

vi.mock("../../api/identity", () => ({ fetchAccountStatus: vi.fn() }));
vi.mock("../../api/room", () => ({ saveUserProfile: vi.fn() }));
vi.mock("../../lib/deviceIdentity", () => ({
  rememberGuestProfile: vi.fn(),
  rememberStartupIdentitySelection: vi.fn(),
}));
vi.mock("./GoogleAccountSettings", () => ({
  default: () => <section aria-label="공개 계정 연결" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("StartupIdentityGate", () => {
  it("does not show identity choices while an existing account is still being checked", () => {
    vi.mocked(fetchAccountStatus).mockReturnValue(new Promise(() => undefined));

    render(<StartupIdentityGate deviceToken="device-1" onComplete={vi.fn()} />);

    expect(screen.queryByRole("main", { name: "시작 로그인" })).toBeNull();
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("keeps the product gated until a local guest identity is persisted", async () => {
    vi.mocked(fetchAccountStatus).mockResolvedValue({
      account: null,
      google: { enabled: false, client_id: "", unavailable_reason: "" },
    });
    vi.mocked(saveUserProfile).mockResolvedValue({} as never);
    const onComplete = vi.fn();

    render(<StartupIdentityGate deviceToken="device-1" onComplete={onComplete} />);

    const name = await screen.findByRole("textbox", { name: "표시 이름" });
    expect(onComplete).not.toHaveBeenCalled();
    await userEvent.type(name, "Local Guest");
    await userEvent.click(screen.getByRole("button", { name: "게스트로 계속" }));

    expect(rememberGuestProfile).toHaveBeenCalledWith({ displayName: "Local Guest" });
    expect(saveUserProfile).toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("resumes an already linked account without asking for a guest name", async () => {
    vi.mocked(fetchAccountStatus).mockResolvedValue({
      account: {
        account_id: "acct-1",
        provider: "google",
        display_name: "Linked User",
        email: "linked@example.test",
        avatar_image_url: "",
      },
      google: { enabled: true, client_id: "client", unavailable_reason: "" },
    });
    const onComplete = vi.fn();

    render(<StartupIdentityGate deviceToken="device-1" onComplete={onComplete} />);

    await vi.waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(rememberStartupIdentitySelection).toHaveBeenCalledOnce();
    expect(screen.queryByRole("textbox", { name: "표시 이름" })).toBeNull();
  });
});
