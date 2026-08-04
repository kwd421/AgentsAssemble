import { useState } from "react";
import { Folder } from "lucide-react";
import { chooseLocalWorkspace } from "../../api";

export default function WorkspacePickerField({
  value,
  disabled = false,
  description = "",
  onChange,
  onError,
}: {
  value: string;
  disabled?: boolean;
  description?: string;
  onChange: (path: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function chooseWorkspace() {
    if (busy || disabled) return;
    setBusy(true);
    onError("");
    try {
      const selected = await chooseLocalWorkspace();
      if (selected.selected && selected.path) onChange(selected.path);
    } catch (error) {
      onError(error instanceof Error ? error.message : "작업 폴더를 선택하지 못했습니다");
    } finally {
      setBusy(false);
    }
  }

  return (
    <label className="dc-agent-field">
      <span>작업 폴더</span>
      <div className="dc-agent-folder-field">
        <Folder size={16} aria-hidden="true" />
        <input
          aria-label="선택한 작업 폴더"
          value={value}
          placeholder="선택되지 않음"
          readOnly
        />
        <button type="button" disabled={busy || disabled} onClick={() => void chooseWorkspace()}>
          {busy ? "선택 중..." : "폴더 선택"}
        </button>
      </div>
      {description && <small>{description}</small>}
    </label>
  );
}
