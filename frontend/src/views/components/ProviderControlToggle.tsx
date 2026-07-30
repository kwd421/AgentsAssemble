import type { ProviderControlOption } from "../../roomSocketClient";

export default function ProviderControlToggle({
  label,
  options,
  value,
  disabled = false,
  onChange,
}: {
  label: string;
  options: ProviderControlOption[];
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const offOption =
    options.find((option) => option.value === "default") || options[0];
  const onOption = options.find((option) => option.value !== offOption?.value);
  const selectedOption = options.find((option) => option.value === value);
  const checked = Boolean(onOption && selectedOption?.value === onOption.value);
  const controlDisabled =
    disabled || !offOption || (!onOption && Boolean(selectedOption));

  return (
    <button
      type="button"
      className="dc-provider-control-toggle"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      data-on={checked}
      disabled={controlDisabled}
      onClick={() => {
        if (!offOption) return;
        if (!selectedOption || !onOption) {
          onChange(offOption.value);
          return;
        }
        onChange(checked ? offOption.value : onOption.value);
      }}
    >
      <span>{selectedOption?.label || "선택 필요"}</span>
      <span className="dc-provider-control-switch" aria-hidden="true">
        <i />
      </span>
    </button>
  );
}
