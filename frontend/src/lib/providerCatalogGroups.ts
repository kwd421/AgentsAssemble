import { Cloud, CreditCard, HardDrive } from "lucide-react";
import type { NativeCliProviderAvailability } from "../roomSocketClient";

export type ProviderCatalogGroup = "subscription" | "api" | "local";

export const PROVIDER_GROUPS = [
  { id: "subscription", label: "Subscription", Icon: CreditCard },
  { id: "api", label: "API", Icon: Cloud },
  { id: "local", label: "Local", Icon: HardDrive },
] as const;

export function providerCatalogGroup(
  provider: NativeCliProviderAvailability | undefined
): ProviderCatalogGroup {
  if (provider?.catalog_group) return provider.catalog_group;
  return provider?.runtime_kind === "api" ? "api" : "subscription";
}

export function projectProvidersByCatalogGroup(
  providers: NativeCliProviderAvailability[]
): Record<ProviderCatalogGroup, NativeCliProviderAvailability[]> {
  return {
    subscription: projectProviders(providers, "subscription"),
    api: projectProviders(providers, "api"),
    local: projectProviders(providers, "local"),
  };
}

function projectProviders(
  providers: NativeCliProviderAvailability[],
  group: ProviderCatalogGroup
): NativeCliProviderAvailability[] {
  return providers.flatMap((provider) => {
    const projected = projectProviderToCatalogGroup(provider, group);
    return projected ? [projected] : [];
  });
}

function projectProviderToCatalogGroup(
  provider: NativeCliProviderAvailability,
  group: ProviderCatalogGroup
): NativeCliProviderAvailability | null {
  const modelControl = provider.controls.find((control) => control.key === "model");
  if (!modelControl) {
    return providerCatalogGroup(provider) === group ? provider : null;
  }
  const providerGroup = providerCatalogGroup(provider);
  const scopedOptions = modelControl.options.filter((option) => {
    const optionGroup = option.metadata?.catalog_group;
    return (
      (typeof optionGroup === "string" && optionGroup ? optionGroup : providerGroup) === group
    );
  });
  if (scopedOptions.length === 0) return null;
  if (scopedOptions.length === modelControl.options.length && providerGroup === group) {
    return provider;
  }
  const defaultModel = scopedOptions.some(
    (option) => option.value === modelControl.default_value
  )
    ? modelControl.default_value
    : scopedOptions[0].value;
  return {
    ...provider,
    catalog_group: group,
    default_model: defaultModel,
    controls: provider.controls.map((control) =>
      control.key === "model"
        ? {
            ...control,
            default_value: defaultModel,
            options: scopedOptions,
          }
        : control
    ),
  };
}

export function providerGroupLabel(group: ProviderCatalogGroup): string {
  return PROVIDER_GROUPS.find((item) => item.id === group)?.label || group;
}
