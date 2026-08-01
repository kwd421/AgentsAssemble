import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import AgentCreateModal from "./AgentCreateModal";

const apiMocks = vi.hoisted(() => ({
  fetchProviderCredentialStatus: vi.fn(),
  setProviderCredential: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  fetchProviderCredentialStatus: apiMocks.fetchProviderCredentialStatus,
  setProviderCredential: apiMocks.setProviderCredential,
}));

afterEach(cleanup);

beforeEach(() => {
  apiMocks.fetchProviderCredentialStatus.mockReset();
  apiMocks.fetchProviderCredentialStatus.mockResolvedValue({
    configured: false,
    source: "missing",
  });
  apiMocks.setProviderCredential.mockReset();
  apiMocks.setProviderCredential.mockResolvedValue({
    configured: true,
    source: "keyring",
  });
});

it("creates a custom API agent from a full completion endpoint and model id", async () => {
  const onCreate = vi.fn().mockResolvedValue(undefined);
  render(
    <AgentCreateModal
      open
      meetingId="room-a"
      roomLabel="Room A"
      catalogRevision="cat-custom"
      providers={[customApiProvider()]}
      onClose={() => undefined}
      onCreate={onCreate}
    />
  );

  await userEvent.click(screen.getByRole("listitem", { name: "API" }));
  await userEvent.click(screen.getByRole("listitem", { name: "Custom API" }));
  await userEvent.type(
    screen.getByLabelText("API 주소"),
    "https://api.example.com/v1/chat/completions"
  );
  await userEvent.type(screen.getByLabelText("모델 ID"), "vendor-model");
  await userEvent.type(screen.getByLabelText("API 키"), "private-custom-key");
  await userEvent.click(screen.getByRole("button", { name: "보안 저장" }));
  await userEvent.click(primaryActionButton());

  await waitFor(() =>
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "custom_api",
        providerEndpoint: "https://api.example.com/v1/chat/completions",
        modelId: "vendor-model",
        maxOutputTokens: 4096,
      })
    )
  );
  expect(apiMocks.setProviderCredential).toHaveBeenCalledWith(
    "custom_api",
    "private-custom-key"
  );
});

function primaryActionButton(): HTMLButtonElement {
  const button = screen
    .getByRole("dialog", { name: "에이전트 추가" })
    .querySelector<HTMLButtonElement>(".dc-agent-create-primary");
  if (!button) throw new Error("Agent create primary action was not rendered");
  return button;
}

function customApiProvider(): NativeCliProviderAvailability {
  return {
    id: "custom_api",
    display_name: "Custom API",
    provider_kind: "custom_openai_api",
    runtime_kind: "api",
    catalog_group: "api",
    connection_kind: "native_cli_bridge",
    executable: "",
    default_model: "",
    interactive: true,
    startable: true,
    available: true,
    workspace_required: false,
    custom_endpoint: true,
    custom_model: true,
    controls: [
      {
        key: "max_output_tokens",
        label: "최대 응답 길이",
        kind: "select",
        default_value: "4096",
        options: [{ value: "4096", label: "4,096 토큰" }],
      },
    ],
  };
}
