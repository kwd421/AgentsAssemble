import type {
  NativeCliProviderAvailability,
  ProviderControl,
} from "../roomSocketClient";

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
  const mayUseDefault = useDefault;
  if (mayUseDefault) {
    const defaultOption = options.find((option) => option.value === control.default_value);
    if (defaultOption) return defaultOption.value;
  }
  return "";
}
