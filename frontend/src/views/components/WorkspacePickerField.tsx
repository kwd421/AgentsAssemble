import { useState } from "react";
import { Folder } from "lucide-react";
import { chooseLocalWorkspace } from "../../api";

function workspacePickerErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("workspace_picker_timeout")) {
    return "폴더 선택 창이 응답하지 않습니다. 창을 닫고 다시 시도해 주세요.";
  }
  if (
    message.includes("workspace_picker_unavailable") ||
    message.includes("workspace_picker_unsupported_platform")
  ) {
    return "이 기기에서는 폴더 선택 창을 열 수 없습니다.";
  }
  if (message.includes("workspace_picker_invalid_selection")) {
    return "선택한 폴더를 사용할 수 없습니다. 다른 폴더를 선택해 주세요.";
  }
  if (message.includes("workspace_picker_failed")) {
    return "폴더 선택 창을 열지 못했습니다. 앱을 앞으로 가져온 뒤 다시 시도해 주세요.";
  }
  return message || "작업 폴더를 선택하지 못했습니다. 다시 시도해 주세요.";
}

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
      onError(workspacePickerErrorMessage(error));
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
