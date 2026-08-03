import type {
  NativeCliProviderAvailability,
  ProviderControl,
} from "../roomSocketClient";

const PASSIVE_STANDARD_CONTROLS: Record<string, ProviderControl> = {
  reasoning_effort: {
    key: "reasoning_effort",
    label: "추론 강도",
    kind: "select",
    options: [{ value: "", label: "기본" }],
    default_value: "",
  },
  service_tier: {
    key: "service_tier",
    label: "응답 속도",
    kind: "select",
    options: [{ value: "", label: "기본" }],
    default_value: "",
  },
};

export function displayProviderControls(
  provider: NativeCliProviderAvailability
): ProviderControl[] {
  const controlsByKey = new Map(
    provider.controls.map((control) => [control.key, control])
  );
  const standardKeys = [
    "model",
    "reasoning_effort",
    "service_tier",
    "permission_mode",
  ];
  const standard = standardKeys.flatMap((key) => {
    const control = controlsByKey.get(key) || PASSIVE_STANDARD_CONTROLS[key];
    return control ? [control] : [];
  });
  const providerSpecific = provider.controls.filter(
    (control) => !standardKeys.includes(control.key)
  );
  return [...standard.slice(0, 3), ...providerSpecific, ...standard.slice(3)];
}

export function initializeProviderSettings(
  provider: NativeCliProviderAvailability
): Record<string, string> {
  return normalizeProviderSettings(provider, {}, true);
}

export function reconcileProviderSettings(
  provider: NativeCliProviderAvailability,
  candidate: Record<string, string>,
  _changedKey = ""
): Record<string, string> {
  return normalizeProviderSettings(provider, candidate, false);
}

export function canonicalProviderModelValue(
  provider: NativeCliProviderAvailability,
  candidate: string
): string {
  const modelControl = provider.controls.find((control) => control.key === "model");
  if (!modelControl || !candidate) return candidate;
  return matchingModelOptionValue(modelControl.options, candidate) || candidate;
}

export function effectiveProviderControlOptions(
  provider: NativeCliProviderAvailability,
  control: ProviderControl,
  settings: Record<string, string>
): ProviderControl["options"] {
  if (!["reasoning_effort", "service_tier"].includes(control.key)) {
    return control.options;
  }
  const modelControl = provider.controls.find((item) => item.key === "model");
  const model = modelControl?.options.find((option) => option.value === settings.model);
  if (control.key === "service_tier") {
    const variants = model?.metadata?.runtime_variants;
    if (Array.isArray(variants)) {
      const selectedEffort = settings.reasoning_effort || "default";
      const allowed = new Set(
        variants
          .filter(
            (variant): variant is Record<string, unknown> =>
              Boolean(variant) &&
              typeof variant === "object" &&
              String(variant.reasoning_effort || "default") === selectedEffort
          )
          .map((variant) => String(variant.service_tier || "default"))
      );
      return control.options.filter((option) => allowed.has(option.value));
    }
  }
  const metadataKey =
    control.key === "reasoning_effort" ? "reasoning_efforts" : "service_tiers";
  const relation = model?.metadata?.[metadataKey];
  if (!Array.isArray(relation)) return control.options;
  const allowed = new Set(relation.map(String));
  if (control.key === "reasoning_effort" && allowed.size === 0) {
    allowed.add("");
  }
  return control.options.filter(
    (option) =>
      allowed.has(option.value) ||
      (control.key === "service_tier" && option.value === "default")
  );
}

function normalizeProviderSettings(
  provider: NativeCliProviderAvailability,
  candidate: Record<string, string>,
  useDefaults: boolean
): Record<string, string> {
  const next: Record<string, string> = {};
  const modelControl = provider.controls.find((control) => control.key === "model");
  if (modelControl) {
    next.model = validControlValue(
      modelControl,
      modelControl.options,
      candidate.model,
      useDefaults
    );
  }
  for (const control of orderedDependentControls(provider)) {
    const options = effectiveProviderControlOptions(provider, control, {
      ...candidate,
      ...next,
    });
    next[control.key] = validControlValue(
      control,
      options,
      candidate[control.key],
      useDefaults
    );
  }
  return next;
}

function orderedDependentControls(provider: NativeCliProviderAvailability): ProviderControl[] {
  const reasoning = provider.controls.find((control) => control.key === "reasoning_effort");
  const serviceTier = provider.controls.find((control) => control.key === "service_tier");
  return [
    ...(reasoning ? [reasoning] : []),
    ...(serviceTier ? [serviceTier] : []),
    ...provider.controls.filter(
      (control) => !["model", "reasoning_effort", "service_tier"].includes(control.key)
    ),
  ];
}

function validControlValue(
  control: ProviderControl,
  options: ProviderControl["options"],
  candidate: string | undefined,
  useDefault: boolean
): string {
  if (candidate !== undefined && options.some((option) => option.value === candidate)) {
    return candidate;
  }
  if (control.key === "model" && candidate) {
    const modelValue = matchingModelOptionValue(options, candidate);
    if (modelValue) return modelValue;
  }
  const mayUseDefault = useDefault;
  if (mayUseDefault) {
    const defaultOption = options.find((option) => option.value === control.default_value);
    if (defaultOption) return defaultOption.value;
  }
  return "";
}

function matchingModelOptionValue(
  options: ProviderControl["options"],
  candidate: string
): string {
  const exact = options.find((option) => option.value === candidate);
  if (exact) return exact.value;
  const candidateKey = modelAliasKey(candidate);
  const aliases = options.filter(
    (option) =>
      modelAliasKey(option.value) === candidateKey ||
      modelAliasKey(option.label) === candidateKey
  );
  return aliases.length === 1 ? aliases[0].value : "";
}

function modelAliasKey(value: string): string {
  return value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}
