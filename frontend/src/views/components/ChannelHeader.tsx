import type { ReactNode } from "react";
import { PanelRight } from "lucide-react";

/**
 * Discord-style channel header: a fixed bar at the top of the central column
 * with the channel name, an optional topic, optional right-aligned actions,
 * and the shell-owned member-list toggle.
 */
export default function ChannelHeader({
  icon,
  title,
  subtitle,
  children,
  membersOpen,
  onToggleMembers,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  children?: ReactNode;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
}) {
  return (
    <header className="dc-chat-head flex h-12 shrink-0 items-center gap-2 px-3 lg:px-4">
      <span className="shrink-0 text-text-muted">{icon}</span>
      <h1 className="shrink-0 text-[15px] font-bold text-text-primary preserve-words">{title}</h1>
      {subtitle && (
        <>
          <span className="hidden h-4 w-px bg-line sm:block" aria-hidden />
          <p className="hidden min-w-0 truncate text-[13px] text-text-muted preserve-words sm:block">
            {subtitle}
          </p>
        </>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-1.5">
        {children}
        {onToggleMembers && (
          <button
            type="button"
            onClick={onToggleMembers}
            aria-label="멤버 목록 토글"
            aria-pressed={membersOpen}
            className={`grid h-8 w-8 place-items-center rounded hover:bg-sidebar-hover ${
              membersOpen ? "text-text-primary" : "text-text-muted"
            }`}
          >
            <PanelRight size={18} />
          </button>
        )}
      </div>
    </header>
  );
}
