import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import ProviderLogo, { providerBrandKey } from "./ProviderLogo";

afterEach(cleanup);

describe("ProviderLogo", () => {
  it("renders a distinct branded mark for every canonical agent provider", () => {
    const providers = [
      ["codex", "codex_live_session"],
      ["antigravity", "antigravity_live_session"],
      ["grok", "grok_live_session"],
      ["claude", "claude_code"],
      ["cursor", "cursor_live_session"],
      ["opencode", "opencode_server"],
      ["deepseek", "deepseek_api"],
      ["cerebras", "cerebras_api"],
      ["ollama", "ollama_api"],
      ["lmstudio", "lmstudio_api"],
      ["llmgateway", "llm_gateway_api"],
    ] as const;
    const { container } = render(
      <div>
        {providers.map(([id, kind]) => (
          <ProviderLogo key={id} providerId={id} providerKind={kind} />
        ))}
      </div>
    );

    providers.forEach(([id]) => {
      expect(container.querySelector(`[data-provider-brand="${id}"]`)).not.toBeNull();
    });
  });

  it("keeps compatibility aliases and rejects unknown providers", () => {
    expect(providerBrandKey(undefined, "codex_cli")).toBe("codex");
    expect(providerBrandKey(undefined, "local_cli")).toBeUndefined();
  });
});
