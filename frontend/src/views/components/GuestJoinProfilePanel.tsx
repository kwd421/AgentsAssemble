import { useState } from "react";
import { ImagePlus, LogIn } from "lucide-react";
import { uploadLobbyAttachment } from "../../api";
import ImageCropper from "./ImageCropper";

type GuestJoinProfilePanelProps = {
  displayName: string;
  avatarImage?: string;
  status?: string;
  busy?: boolean;
  onDisplayNameChange: (value: string) => void;
  onAvatarImageChange: (value: string) => void;
  onJoin: () => void;
};

export default function GuestJoinProfilePanel({
  displayName,
  avatarImage,
  status = "",
  busy = false,
  onDisplayNameChange,
  onAvatarImageChange,
  onJoin,
}: GuestJoinProfilePanelProps) {
  const [cropFile, setCropFile] = useState<File | null>(null);
  const [uploadStatus, setUploadStatus] = useState("");
  const avatarLabel = (displayName || "G").slice(0, 1).toUpperCase() || "G";

  async function handleCropped(file: File) {
    setUploadStatus("프로필 사진 저장 중...");
    try {
      const attachment = await uploadLobbyAttachment(file);
      onAvatarImageChange(attachment.url);
      setCropFile(null);
      setUploadStatus("프로필 사진 저장됨");
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : "프로필 사진 저장 실패");
    }
  }

  return (
    <div className="dc-guest-join-panel">
      <section className="dc-guest-join-card" aria-label="입장 프로필">
        <h1>입장 프로필</h1>
        <div className="dc-guest-avatar-row">
          <span className="dc-guest-avatar" data-has-image={Boolean(avatarImage)}>
            {avatarImage ? <img src={avatarImage} alt="" /> : avatarLabel}
          </span>
          <label className="dc-member-session-button">
            <ImagePlus size={15} />
            프로필 사진
            <input
              className="sr-only"
              type="file"
              accept="image/*"
              onChange={(event) => {
                const file = event.currentTarget.files?.[0] || null;
                if (file) setCropFile(file);
                event.currentTarget.value = "";
              }}
            />
          </label>
        </div>
        {cropFile && (
          <ImageCropper
            file={cropFile}
            onCancel={() => setCropFile(null)}
            onCropped={(file) => void handleCropped(file)}
          />
        )}
        <label className="dc-guest-name-field">
          이름
          <input
            type="text"
            maxLength={80}
            value={displayName}
            onChange={(event) => onDisplayNameChange(event.currentTarget.value)}
            placeholder="방에서 보일 이름"
          />
        </label>
        <button
          type="button"
          className="dc-guest-join-button"
          disabled={busy || !displayName.trim()}
          onClick={onJoin}
        >
          <LogIn size={16} />
          입장
        </button>
        {(status || uploadStatus) && (
          <p className="dc-member-session-status preserve-words">{uploadStatus || status}</p>
        )}
      </section>
    </div>
  );
}
