import { useEffect, useId, useRef, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";
import { MoreHorizontal, Pencil, Trash2, X } from "lucide-react";

import type { LobbyEvent } from "../../api";
import "./MessageMutationControls.css";

type MutationKind = "edit" | "delete" | null;

export default function MessageMutationControls({
  event,
  canEdit,
  canDelete,
  onEdit,
  onDelete,
}: {
  event: LobbyEvent;
  canEdit: boolean;
  canDelete: boolean;
  onEdit: (content: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const titleId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [mutation, setMutation] = useState<MutationKind>(null);
  const [draft, setDraft] = useState(event.message);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!menuOpen) return;
    function closeMenu(pointer: PointerEvent) {
      if (!menuRef.current?.contains(pointer.target as Node)) setMenuOpen(false);
    }
    window.addEventListener("pointerdown", closeMenu);
    return () => window.removeEventListener("pointerdown", closeMenu);
  }, [menuOpen]);

  useEffect(() => {
    if (!mutation) return;
    function closeOnEscape(keyboard: KeyboardEvent) {
      if (keyboard.key === "Escape" && !busy) setMutation(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, mutation]);

  async function runEdit() {
    if (busy || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      await onEdit(draft);
      setMutation(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "메시지를 수정하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function runDelete(showDialogOnFailure = false) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onDelete();
      setMutation(null);
    } catch (reason) {
      if (showDialogOnFailure) setMutation("delete");
      setError(reason instanceof Error ? reason.message : "메시지를 삭제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function requestDelete(mouse: MouseEvent<HTMLButtonElement>) {
    setMenuOpen(false);
    if (mouse.shiftKey) {
      void runDelete(true);
      return;
    }
    setMutation("delete");
  }

  if (!canEdit && !canDelete) return null;

  return (
    <>
      <div className="dc-message-mutation" ref={menuRef}>
        <button
          type="button"
          className="dc-message-action-button"
          aria-label="메시지 메뉴"
          title="더 보기"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <MoreHorizontal size={15} />
        </button>
        {menuOpen && (
          <div className="dc-message-mutation-menu" role="menu">
            {canEdit && (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setDraft(event.message);
                  setMenuOpen(false);
                  setMutation("edit");
                }}
              >
                <Pencil size={15} />
                수정
              </button>
            )}
            {canDelete && (
              <button type="button" role="menuitem" className="danger" onClick={requestDelete}>
                <Trash2 size={15} />
                삭제
              </button>
            )}
          </div>
        )}
      </div>

      {mutation && createPortal(
        <div
          className="dc-modal-backdrop"
          role="presentation"
          onMouseDown={() => {
            if (!busy) setMutation(null);
          }}
        >
          <section
            className="dc-message-mutation-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            onMouseDown={(mouse) => mouse.stopPropagation()}
          >
            <header>
              <div>
                <h2 id={titleId}>{mutation === "edit" ? "메시지 수정하기" : "메시지 삭제하기"}</h2>
                <p>{mutation === "edit" ? "수정해도 에이전트를 다시 호출하지 않습니다." : "정말 이 메시지를 삭제할까요?"}</p>
              </div>
              <button
                type="button"
                className="dc-settings-close"
                aria-label="닫기"
                disabled={busy}
                onClick={() => setMutation(null)}
              >
                <X size={20} />
              </button>
            </header>

            {mutation === "edit" ? (
              <textarea
                autoFocus
                value={draft}
                maxLength={12_000}
                disabled={busy}
                onChange={(change) => setDraft(change.target.value)}
              />
            ) : (
              <div className="dc-message-delete-preview">
                <p>
                  <strong>{event.name}</strong>
                  <time>{new Date(event.created_at).toLocaleString("ko-KR")}</time>
                </p>
                <div>
                  {event.kind === "vote"
                    ? event.vote_question || "투표"
                    : event.message || "첨부파일 메시지"}
                </div>
              </div>
            )}

            {mutation === "delete" && (
              <p className="dc-message-delete-hint">
                참고: 데스크톱에서는 Shift를 누른 채 삭제를 선택하면 이 확인을 건너뜁니다.
              </p>
            )}
            {error && <p className="dc-channel-composer-error" role="alert">{error}</p>}

            <footer>
              <button type="button" className="ops-button" disabled={busy} onClick={() => setMutation(null)}>
                취소
              </button>
              <button
                type="button"
                className={mutation === "delete" ? "ops-button dc-message-delete-confirm" : "ops-cta"}
                disabled={busy || (mutation === "edit" && !draft.trim())}
                onClick={() => void (mutation === "edit" ? runEdit() : runDelete())}
              >
                {busy ? "처리 중..." : mutation === "edit" ? "저장" : "삭제"}
              </button>
            </footer>
          </section>
        </div>,
        document.body
      )}
    </>
  );
}
