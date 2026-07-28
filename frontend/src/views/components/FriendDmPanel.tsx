import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Send, UserCircle } from "lucide-react";
import {
  fetchRoomFriendDm,
  type RoomFriend,
  type RoomFriendDmEvent,
} from "../../api";
import { postCurrentUserFriendDm } from "../../app/currentUserFriendDm";
import {
  clearFriendDmDraft,
  friendDmDraftValue,
  updateFriendDmDraft,
  type FriendDmDrafts,
} from "../../lib/friendDmDraftModel";
import { participantTypeMeta } from "../../lib/participantTypes";
import { isActivePresence } from "../../lib/presenceStatus";
import DiscordText from "./DiscordText";

function timeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const AI_DM_TYPES = new Set(["subscription_ai", "api", "local", "remote"]);

function canDirectMessage(friend: RoomFriend) {
  return AI_DM_TYPES.has(friend.participant_type) && Boolean(friend.source_agent_id || friend.agent_id);
}

export default function FriendDmPanel({
  friend,
  focusSignal = 0,
  layout = "card",
  onShowProfile,
}: {
  friend: RoomFriend | null;
  focusSignal?: number;
  layout?: "card" | "channel";
  onShowProfile?: (friend: RoomFriend) => void;
}) {
  const [events, setEvents] = useState<RoomFriendDmEvent[]>([]);
  const [draftsByFriend, setDraftsByFriend] = useState<FriendDmDrafts>({});
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const friendId = friend?.friend_id || "";
  const draft = friendDmDraftValue(draftsByFriend, friendId);
  const meta = friend ? participantTypeMeta(friend.participant_type) : null;
  const Icon = meta?.icon;
  const directMessageEnabled = friend ? canDirectMessage(friend) : false;
  const friendIsActive = friend ? isActivePresence(friend.status) : false;
  const placeholder = useMemo(
    () => {
      if (!friend) return "저장된 친구를 선택하세요";
      if (!directMessageEnabled) return "실제 AI 세션이 있는 친구만 DM할 수 있습니다";
      return `${friend.display_name}에게 DM`;
    },
    [directMessageEnabled, friend]
  );

  useEffect(() => {
    let cancelled = false;
    if (!friendId) {
      setEvents([]);
      setStatus("");
      return;
    }
    function load(showLoading: boolean) {
      if (showLoading) {
        setLoading(true);
        setStatus("");
      }
      fetchRoomFriendDm(friendId)
        .then((payload) => {
          if (cancelled) return;
          setEvents(payload.events);
        })
        .catch((error) => {
          if (cancelled) return;
          if (showLoading) setEvents([]);
          setStatus(error instanceof Error ? error.message : "DM을 불러오지 못했습니다");
        })
        .finally(() => {
          if (!cancelled && showLoading) setLoading(false);
        });
    }
    load(true);
    const interval = window.setInterval(() => load(false), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [friendId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [events.length, friendId]);

  useEffect(() => {
    if (!friendId || !focusSignal) return;
    inputRef.current?.focus();
  }, [focusSignal, friendId]);

  function setDraft(nextDraft: string) {
    setDraftsByFriend((previous) => updateFriendDmDraft(previous, friendId, nextDraft));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!friend || posting) return;
    if (!directMessageEnabled) {
      setStatus("실제 AI 세션을 찾을 수 없습니다.");
      return;
    }
    const message = draft.trim();
    if (!message) return;
    setPosting(true);
    setStatus(friendIsActive ? "DM 전달 중..." : "세션 재개 중...");
    try {
      const payload = await postCurrentUserFriendDm({
        friendId: friend.friend_id,
        message,
        resumeIfNeeded: true,
      });
      setEvents(payload.events);
      setDraftsByFriend((previous) => clearFriendDmDraft(previous, friend.friend_id));
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "DM 전송 실패");
    } finally {
      setPosting(false);
    }
  }

  if (!friend) return null;

  return (
    <section className="dc-friend-dm-panel" data-layout={layout} aria-label={`${friend.display_name} DM`}>
      <header className="dc-friend-dm-head">
        <div>
          <h3>{layout === "channel" ? friend.display_name : "DM"}</h3>
          <p>
            {layout === "channel"
              ? `${meta?.label || "저장된 친구"} · 1:1 AI DM`
              : "저장된 AI 세션과 직접 대화합니다."}
          </p>
        </div>
        <div className="dc-friend-dm-head-actions">
          {loading && <span>동기화 중</span>}
          {layout === "channel" && onShowProfile && (
            <button
              type="button"
              className="dc-friend-dm-profile-button"
              onClick={() => onShowProfile(friend)}
              aria-label={`${friend.display_name} 프로필 보기`}
              title="프로필 보기"
            >
              <UserCircle size={17} />
            </button>
          )}
        </div>
      </header>
      <div ref={scrollRef} className="dc-friend-dm-feed">
        {layout === "channel" && (
          <div className="dc-friend-dm-intro">
            <span className="dc-friend-dm-intro-avatar" data-type={friend.participant_type}>
              {Icon ? <Icon size={28} /> : friend.display_name.slice(0, 1).toUpperCase()}
            </span>
            <h2 className="preserve-words">{friend.display_name}</h2>
            <p className="preserve-words">
              저장된 AI 세션에게 직접 보내는 1:1 DM입니다. 꺼져 있으면 가능한 경우 기존 세션 재개를 먼저 시도합니다.
            </p>
          </div>
        )}
        {events.length ? (
          events.map((item) => (
            <article key={item.id} className="dc-friend-dm-message" data-side={item.side}>
              <div className="dc-friend-dm-message-meta">
                <strong className="preserve-words">{item.name || (item.side === "mine" ? "나" : friend.display_name)}</strong>
                <time>{timeLabel(item.created_at)}</time>
              </div>
              <div className="preserve-words">
                <DiscordText text={item.message || ""} />
              </div>
            </article>
          ))
        ) : (
          <p className="dc-friend-dm-empty">
            아직 DM이 없습니다.
          </p>
        )}
      </div>
      {status && <p className="dc-friend-dm-status preserve-words">{status}</p>}
      <form className="dc-friend-dm-composer" onSubmit={submit}>
        <input
          ref={inputRef}
          aria-label={`${friend.display_name} DM 입력`}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder}
          maxLength={2000}
          disabled={!directMessageEnabled}
        />
        <button type="submit" disabled={posting || !directMessageEnabled || !draft.trim()} aria-label="DM 보내기">
          <Send size={16} />
        </button>
      </form>
    </section>
  );
}
