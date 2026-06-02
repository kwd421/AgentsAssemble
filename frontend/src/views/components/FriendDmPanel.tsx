import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Send } from "lucide-react";
import {
  fetchRoomFriendDm,
  postRoomFriendDm,
  type RoomFriend,
  type RoomFriendDmEvent,
} from "../../api";

function timeLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function FriendDmPanel({ friend }: { friend: RoomFriend | null }) {
  const [events, setEvents] = useState<RoomFriendDmEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const friendId = friend?.friend_id || "";
  const placeholder = useMemo(
    () => (friend ? `${friend.display_name}에게 로컬 메시지` : "저장된 친구를 선택하세요"),
    [friend]
  );

  useEffect(() => {
    let cancelled = false;
    if (!friendId) {
      setEvents([]);
      setStatus("");
      return;
    }
    setLoading(true);
    fetchRoomFriendDm(friendId)
      .then((payload) => {
        if (cancelled) return;
        setEvents(payload.events);
        setStatus("");
      })
      .catch((error) => {
        if (cancelled) return;
        setEvents([]);
        setStatus(error instanceof Error ? error.message : "로컬 DM을 불러오지 못했습니다");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [friendId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [events.length, friendId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!friend || posting) return;
    const message = draft.trim();
    if (!message) return;
    setPosting(true);
    setStatus("");
    try {
      const payload = await postRoomFriendDm({
        friendId: friend.friend_id,
        message,
        name: "SeiNel",
        side: "mine",
      });
      setEvents(payload.events);
      setDraft("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "로컬 DM 전송 실패");
    } finally {
      setPosting(false);
    }
  }

  if (!friend) return null;

  return (
    <section className="dc-friend-dm-panel" aria-label={`${friend.display_name} 로컬 DM`}>
      <header className="dc-friend-dm-head">
        <div>
          <h3>로컬 DM</h3>
          <p>외부 Discord로 전송되지 않습니다.</p>
        </div>
        {loading && <span>동기화 중</span>}
      </header>
      <div ref={scrollRef} className="dc-friend-dm-feed">
        {events.length ? (
          events.map((item) => (
            <article key={item.id} className="dc-friend-dm-message" data-side={item.side}>
              <div className="dc-friend-dm-message-meta">
                <strong className="preserve-words">{item.name || (item.side === "mine" ? "나" : friend.display_name)}</strong>
                <time>{timeLabel(item.created_at)}</time>
              </div>
              <p className="preserve-words">{item.message}</p>
            </article>
          ))
        ) : (
          <p className="dc-friend-dm-empty">
            아직 로컬 DM이 없습니다. 저장된 세션에게 다시 말을 걸기 위한 메모를 남겨둘 수 있습니다.
          </p>
        )}
      </div>
      {status && <p className="dc-friend-dm-status preserve-words">{status}</p>}
      <form className="dc-friend-dm-composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder}
          maxLength={2000}
        />
        <button type="submit" disabled={posting || !draft.trim()} aria-label="로컬 DM 보내기">
          <Send size={16} />
        </button>
      </form>
    </section>
  );
}
