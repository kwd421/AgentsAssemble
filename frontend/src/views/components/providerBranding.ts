import claudeLogo from "../../assets/provider-logos/claude.svg";
import cursorLogo from "../../assets/provider-logos/cursor.png";
import deepSeekLogo from "../../assets/provider-logos/deepseek.png";
import geminiLogo from "../../assets/provider-logos/gemini.webp";
import grokLogo from "../../assets/provider-logos/grok.png";
import openAILogo from "../../assets/provider-logos/openai.svg";
import openCodeLogo from "../../assets/provider-logos/opencode.png";

export type ProviderBrandKey =
  | "codex"
  | "antigravity"
  | "grok"
  | "claude"
  | "cursor"
  | "opencode"
  | "deepseek";

export type ProviderBrand = {
  label: string;
  logo: string;
  background: string;
  scale: string;
};

const PROVIDER_ALIASES: Record<string, ProviderBrandKey> = {
  codex: "codex",
  codex_cli: "codex",
  codex_live_session: "codex",
  antigravity: "antigravity",
  antigravity_cli: "antigravity",
  antigravity_live_session: "antigravity",
  agy: "antigravity",
  grok: "grok",
  grok_live_session: "grok",
  claude: "claude",
  claude_code: "claude",
  cursor: "cursor",
  cursor_live_session: "cursor",
  opencode: "opencode",
  opencode_server: "opencode",
  deepseek: "deepseek",
  deepseek_api: "deepseek",
};

export const PROVIDER_BRANDS: Record<ProviderBrandKey, ProviderBrand> = {
  codex: {
    label: "OpenAI",
    logo: openAILogo,
    background: "#000000",
    scale: "100%",
  },
  antigravity: {
    label: "Google Gemini",
    logo: geminiLogo,
    background: "#ffffff",
    scale: "72%",
  },
  grok: {
    label: "Grok",
    logo: grokLogo,
    background: "#000000",
    scale: "100%",
  },
  claude: {
    label: "Claude",
    logo: claudeLogo,
    background: "#d97757",
    scale: "100%",
  },
  cursor: {
    label: "Cursor",
    logo: cursorLogo,
    background: "#0f0e0b",
    scale: "100%",
  },
  opencode: {
    label: "OpenCode",
    logo: openCodeLogo,
    background: "#171515",
    scale: "100%",
  },
  deepseek: {
    label: "DeepSeek",
    logo: deepSeekLogo,
    background: "#ffffff",
    scale: "76%",
  },
};

function normalizeProviderIdentifier(value?: string) {
  return String(value || "").trim().toLowerCase().replaceAll("-", "_");
}

export function providerBrandKey(
  providerId?: string,
  providerKind?: string
): ProviderBrandKey | undefined {
  return (
    PROVIDER_ALIASES[normalizeProviderIdentifier(providerId)] ||
    PROVIDER_ALIASES[normalizeProviderIdentifier(providerKind)]
  );
}
