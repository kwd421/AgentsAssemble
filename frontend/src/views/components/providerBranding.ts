import claudeLogo from "../../assets/provider-logos/claude.svg";
import cerebrasLogo from "../../assets/provider-logos/cerebras.svg";
import cursorLogo from "../../assets/provider-logos/cursor.png";
import deepSeekLogo from "../../assets/provider-logos/deepseek.png";
import geminiLogo from "../../assets/provider-logos/gemini.webp";
import grokLogo from "../../assets/provider-logos/grok.png";
import lmStudioLogo from "../../assets/provider-logos/lmstudio.png";
import llmGatewayLogo from "../../assets/provider-logos/llmgateway.svg";
import ollamaLogo from "../../assets/provider-logos/ollama.png";
import openAILogo from "../../assets/provider-logos/openai.svg";
import openCodeLogo from "../../assets/provider-logos/opencode.png";
import openRouterLogo from "../../assets/provider-logos/openrouter.svg";
import vercelLogo from "../../assets/provider-logos/vercel.svg";

export type ProviderBrandKey =
  | "codex"
  | "antigravity"
  | "grok"
  | "claude"
  | "cursor"
  | "opencode"
  | "deepseek"
  | "cerebras"
  | "ollama"
  | "lmstudio"
  | "llmgateway"
  | "openrouter"
  | "vercel";

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
  cerebras: "cerebras",
  cerebras_api: "cerebras",
  ollama: "ollama",
  ollama_api: "ollama",
  lmstudio: "lmstudio",
  lmstudio_api: "lmstudio",
  lm_studio: "lmstudio",
  llmgateway: "llmgateway",
  llm_gateway_api: "llmgateway",
  openrouter: "openrouter",
  openrouter_api: "openrouter",
  vercel: "vercel",
  vercel_ai_gateway: "vercel",
};

// Source assets have very different built-in whitespace. These scales normalize
// the contrasting brand mark to roughly 70% of the circular badge, not the raw
// image canvas.
export const PROVIDER_BRANDS: Record<ProviderBrandKey, ProviderBrand> = {
  codex: {
    label: "OpenAI",
    logo: openAILogo,
    background: "#000000",
    scale: "141%",
  },
  antigravity: {
    label: "Google Gemini",
    logo: geminiLogo,
    background: "#ffffff",
    scale: "79%",
  },
  grok: {
    label: "Grok",
    logo: grokLogo,
    background: "#000000",
    scale: "133%",
  },
  claude: {
    label: "Claude",
    logo: claudeLogo,
    background: "#d97757",
    scale: "96%",
  },
  cursor: {
    label: "Cursor",
    logo: cursorLogo,
    background: "#0f0e0b",
    scale: "103%",
  },
  opencode: {
    label: "OpenCode",
    logo: openCodeLogo,
    background: "#171515",
    scale: "111%",
  },
  deepseek: {
    label: "DeepSeek",
    logo: deepSeekLogo,
    background: "#ffffff",
    scale: "70%",
  },
  cerebras: {
    label: "Cerebras",
    logo: cerebrasLogo,
    background: "#ffffff",
    scale: "70%",
  },
  ollama: {
    label: "Ollama",
    logo: ollamaLogo,
    background: "#ffffff",
    scale: "87%",
  },
  lmstudio: {
    label: "LM Studio",
    logo: lmStudioLogo,
    background: "#5d45dd",
    scale: "89%",
  },
  llmgateway: {
    label: "LLM Gateway",
    logo: llmGatewayLogo,
    background: "#151515",
    scale: "76%",
  },
  openrouter: {
    label: "OpenRouter",
    logo: openRouterLogo,
    background: "#ffffff",
    scale: "82%",
  },
  vercel: {
    label: "Vercel AI Gateway",
    logo: vercelLogo,
    background: "#000000",
    scale: "74%",
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
