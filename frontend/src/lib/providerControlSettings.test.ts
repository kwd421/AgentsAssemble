import { describe, expect, it } from "vitest";
import type { NativeCliProviderAvailability } from "../roomSocketClient";
import {
  effectiveProviderControlOptions,
  initializeProviderSettings,
  reconcileProviderSettings,
} from "./providerControlSettings";

function relatedProvider(): NativeCliProviderAvailability {
  return {
    id: "codex",
    display_name: "Codex",
    provider_kind: "codex_live_session",
    runtime_kind: "live_cli",
    connection_kind: "native_cli_bridge",
    executable: "codex",
    default_model: "model-low",
    interactive: true,
    startable: true,
    available: true,
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox",
        default_value: "model-low",
        options: [
          {
            value: "model-low",
            label: "Low",
            metadata: {
              reasoning_efforts: ["low"],
              runtime_variants: [
                { reasoning_effort: "low", service_tier: "default" },
              ],
            },
          },
          {
            value: "model-variable",
            label: "Variable",
            metadata: {
              reasoning_efforts: ["low", "high"],
              runtime_variants: [
                { reasoning_effort: "low", service_tier: "default" },
                { reasoning_effort: "high", service_tier: "default" },
                { reasoning_effort: "high", service_tier: "fast" },
              ],
            },
          },
          {
            value: "model-high",
            label: "High",
            metadata: {
              reasoning_efforts: ["high"],
              runtime_variants: [
                { reasoning_effort: "high", service_tier: "default" },
              ],
            },
          },
          {
            value: "model-exact",
            label: "Exact",
            metadata: {
              reasoning_efforts: [],
            },
          },
        ],
      },
      {
        key: "reasoning_effort",
        label: "추론 강도",
        kind: "select",
        default_value: "low",
        options: [
          { value: "low", label: "low" },
          { value: "high", label: "high" },
        ],
      },
      {
        key: "service_tier",
        label: "응답 속도",
        kind: "select",
        default_value: "default",
        options: [
          { value: "default", label: "기본" },
          { value: "fast", label: "Fast" },
        ],
      },
      {
        key: "permission_mode",
        label: "권한",
        kind: "select",
        default_value: "meeting_read_only",
        options: [
          { value: "meeting_read_only", label: "읽기 전용" },
          { value: "workspace_write", label: "작업 폴더 쓰기" },
        ],
      },
    ],
  };
}

describe("providerControlSettings", () => {
  it("initializes every catalog control from its default", () => {
    expect(initializeProviderSettings(relatedProvider())).toEqual({
      model: "model-low",
      reasoning_effort: "low",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    });
  });

  it("clears model-dependent values that require an explicit new choice", () => {
    expect(
      reconcileProviderSettings(
        relatedProvider(),
        {
          model: "model-high",
          reasoning_effort: "low",
          service_tier: "fast",
          permission_mode: "workspace_write",
        },
        "model"
      )
    ).toEqual({
      model: "model-high",
      reasoning_effort: "",
      service_tier: "",
      permission_mode: "workspace_write",
    });
  });

  it("clears service tier when reasoning invalidates it", () => {
    const provider = relatedProvider();
    const next = reconcileProviderSettings(
      provider,
      {
        model: "model-variable",
        reasoning_effort: "low",
        service_tier: "fast",
        permission_mode: "meeting_read_only",
      },
      "reasoning_effort"
    );

    expect(next.service_tier).toBe("");
    expect(
      effectiveProviderControlOptions(
        provider,
        provider.controls.find((control) => control.key === "service_tier")!,
        next
      ).map((option) => option.value)
    ).toEqual(["default"]);
  });

  it("uses the explicit default option for an exact model without reasoning variants", () => {
    const provider = relatedProvider();
    const reasoning = provider.controls.find(
      (control) => control.key === "reasoning_effort"
    )!;
    reasoning.options = [
      { value: "", label: "기본" },
      ...reasoning.options,
    ];

    const next = reconcileProviderSettings(
      provider,
      {
        model: "model-exact",
        reasoning_effort: "high",
        service_tier: "default",
        permission_mode: "meeting_read_only",
      },
      "model"
    );

    expect(next.reasoning_effort).toBe("");
    expect(
      effectiveProviderControlOptions(provider, reasoning, next).map(
        (option) => option.value
      )
    ).toEqual([""]);
  });

  it("leaves catalog-refresh conflicts explicit when no user control changed", () => {
    expect(
      reconcileProviderSettings(relatedProvider(), {
        model: "model-high",
        reasoning_effort: "low",
        service_tier: "default",
        permission_mode: "meeting_read_only",
      }).reasoning_effort
    ).toBe("");
  });

  it("reconciles a stored provider display name with its canonical model id", () => {
    const provider = relatedProvider();
    provider.controls[0].options = [
      {
        value: "claude-opus-4-6-thinking",
        label: "Claude Opus 4.6 Thinking",
        metadata: {
          reasoning_efforts: ["medium"],
        },
      },
    ];
    provider.controls[1].options = [{ value: "medium", label: "medium" }];

    expect(
      reconcileProviderSettings(provider, {
        model: "Claude Opus 4.6 (Thinking)",
        reasoning_effort: "medium",
        service_tier: "default",
        permission_mode: "meeting_read_only",
      })
    ).toEqual({
      model: "claude-opus-4-6-thinking",
      reasoning_effort: "medium",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    });
  });
});
