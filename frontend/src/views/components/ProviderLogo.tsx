import type { ReactNode } from "react";
import { Bot } from "lucide-react";
import {
  PROVIDER_BRANDS,
  providerBrandKey,
  type ProviderBrandKey,
} from "./providerBranding";

export { providerBrandKey };
export type { ProviderBrandKey };

export default function ProviderLogo({
  providerId,
  providerKind,
  size = 24,
  fallback,
  decorative = true,
}: {
  providerId?: string;
  providerKind?: string;
  size?: number;
  fallback?: ReactNode;
  decorative?: boolean;
}) {
  const brandKey = providerBrandKey(providerId, providerKind);
  if (!brandKey) {
    return fallback ?? <Bot size={Math.max(14, Math.round(size * 0.52))} />;
  }
  const brand = PROVIDER_BRANDS[brandKey];
  return (
    <span
      className="dc-provider-logo"
      data-provider-brand={brandKey}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : `${brand.label} 로고`}
      role={decorative ? undefined : "img"}
      style={{
        width: size,
        height: size,
        background: brand.background,
      }}
    >
      <img
        src={brand.logo}
        alt=""
        draggable={false}
        style={{ width: brand.scale, height: brand.scale }}
      />
    </span>
  );
}
