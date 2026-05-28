import { FileDown } from "lucide-react";
import type { LobbyAttachmentRef } from "../../api";

function formatAttachmentSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

export default function LobbyAttachments({
  attachments,
}: {
  attachments?: LobbyAttachmentRef[];
}) {
  const visibleAttachments = (attachments || []).filter((attachment) => attachment.id);
  if (visibleAttachments.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {visibleAttachments.map((attachment) => {
        const sizeLabel = formatAttachmentSize(attachment.size);
        if (attachment.is_image && attachment.url) {
          return (
            <a
              key={attachment.id}
              href={attachment.url}
              target="_blank"
              rel="noreferrer"
              className="ops-inner group max-w-[180px] overflow-hidden rounded-lg border-accent/20 bg-black/20 text-left transition hover:border-accent/45"
              aria-label={`${attachment.filename} 크게 보기`}
            >
              <img
                src={attachment.url}
                alt={attachment.filename}
                loading="lazy"
                className="h-24 w-full object-cover"
              />
              <span className="block truncate px-2 py-1.5 text-[11px] font-bold text-text-secondary preserve-words">
                {attachment.filename}
              </span>
            </a>
          );
        }

        return (
          <a
            key={attachment.id}
            href={attachment.download_url || attachment.url}
            download={attachment.filename}
            className="ops-inner flex max-w-full items-center gap-2 rounded-lg px-3 py-2 text-[12px] font-bold text-text-secondary transition hover:border-accent/45 hover:text-accent"
          >
            <FileDown size={15} className="shrink-0 text-accent" />
            <span className="min-w-0 truncate preserve-words">{attachment.filename}</span>
            {sizeLabel && <span className="shrink-0 text-[10px] text-text-muted">{sizeLabel}</span>}
          </a>
        );
      })}
    </div>
  );
}
