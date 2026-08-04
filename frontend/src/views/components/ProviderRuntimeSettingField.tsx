import type { ProviderControl } from "../../roomSocketClient";
import ProviderControlSelect from "./ProviderControlSelect";
import ProviderControlToggle from "./ProviderControlToggle";

export default function ProviderRuntimeSettingField({
  control,
  options,
  value,
  disabled,
  onChange,
}: {
  control: ProviderControl;
  options: ProviderControl["options"];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span>{control.label}</span>
      {control.key === "service_tier" && options.length <= 2 ? (
        <ProviderControlToggle
          label={control.label}
          options={options}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      ) : (
        <ProviderControlSelect
          label={control.label}
          options={options}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      )}
    </label>
  );
}
