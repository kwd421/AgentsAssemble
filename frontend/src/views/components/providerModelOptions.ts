import type { ProviderControlOption } from "../../roomSocketClient";

export type ProviderOptionGroup = {
  label: string;
  options: ProviderControlOption[];
};

const MODEL_FAMILY_LABELS = [
  ["haiku", "Haiku"],
  ["sonnet", "Sonnet"],
  ["opus", "Opus"],
  ["fable", "Fable"],
  ["gpt", "GPT"],
  ["gemini", "Gemini"],
  ["grok", "Grok"],
  ["deepseek", "DeepSeek"],
  ["qwen", "Qwen"],
  ["glm", "GLM"],
  ["kimi", "Kimi"],
  ["nemotron", "Nemotron"],
  ["llama", "Llama"],
] as const;

export function filterProviderControlOptions(
  controlLabel: string,
  options: ProviderControlOption[],
  query: string,
  freeOnly: boolean,
  visionOnly = false,
  reasoningOnly = false
): ProviderControlOption[] {
  if (controlLabel !== "모델") return options;
  const needle = query.trim().toLocaleLowerCase();
  return options.filter((option) => {
    if (freeOnly && !isFreeProviderOption(option)) return false;
    if (visionOnly && option.metadata?.vision !== true) return false;
    if (reasoningOnly && option.metadata?.reasoning !== true) return false;
    if (!needle) return true;
    const metadata = option.metadata || {};
    return [
      option.label,
      option.value,
      metadata.group,
      metadata.family,
      metadata.description,
    ]
      .filter((value): value is string => typeof value === "string")
      .join(" ")
      .toLocaleLowerCase()
      .includes(needle);
  });
}

export function groupProviderControlOptions(
  controlLabel: string,
  options: ProviderControlOption[]
): ProviderOptionGroup[] {
  if (controlLabel !== "모델") return [{ label: "", options }];
  const groups = new Map<string, ProviderControlOption[]>();
  for (const option of options) {
    const family = modelFamily(option) || "기타";
    groups.set(family, [...(groups.get(family) || []), option]);
  }
  if (groups.size <= 1) return [{ label: "", options }];
  return [...groups].map(([label, groupOptions]) => ({
    label,
    options: groupOptions,
  }));
}

export function isFreeProviderOption(option: ProviderControlOption): boolean {
  return ["free", "free_tier"].includes(String(option.metadata?.pricing || ""));
}

function modelFamily(option: ProviderControlOption): string {
  const explicitGroup = option.metadata?.group;
  if (typeof explicitGroup === "string" && explicitGroup.trim()) {
    return explicitGroup.trim();
  }
  const explicitFamily = option.metadata?.family;
  if (typeof explicitFamily === "string" && explicitFamily.trim()) {
    return explicitFamily.trim();
  }
  const normalized = `${option.value} ${option.label}`.toLowerCase();
  for (const [token, label] of MODEL_FAMILY_LABELS) {
    if (new RegExp(`(^|[^a-z0-9])${token}([^a-z0-9]|$)`).test(normalized)) {
      return label;
    }
  }
  return "";
}
