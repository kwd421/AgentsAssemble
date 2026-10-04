import "./VotePollCard.css";
import { useCallback, useEffect, useState } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
import {
  type LobbyEvent,
  type VoteSummary,
} from "../../api";
import { useRoomSocket } from "../../RoomSocketContext";

function remainingTimeLabel(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}시간 ${minutes}분`;
  if (minutes) return `${minutes}분 ${seconds}초`;
  return `${seconds}초`;
}

export default function VotePollCard({
  event,
  canVote = true,
  canClose = false,
  revision = "",
}: {
  event: LobbyEvent;
  canVote?: boolean;
  canClose?: boolean;
  revision?: string;
}) {
  const roomSocket = useRoomSocket();
  const voteId = event.vote_id || event.id;
  const [summary, setSummary] = useState<VoteSummary | null>(null);
  const [busyOption, setBusyOption] = useState("");
  const [error, setError] = useState("");
  const [clockMs, setClockMs] = useState(() => Date.now());

  const refresh = useCallback(() => {
    if (!roomSocket?.ready()) {
      setError("방 연결이 준비되지 않았습니다.");
      return;
    }
    void roomSocket
      .command("room.vote.summary", { vote_id: voteId })
      .then((ack) => {
        setSummary((ack.result || null) as VoteSummary | null);
        setError("");
      })
      .catch((errorValue) => {
        setError(errorValue instanceof Error ? errorValue.message : "투표 현황을 불러오지 못했습니다.");
      });
  }, [revision, roomSocket, voteId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const deadlineAt = summary?.vote_deadline_at || event.vote_deadline_at || "";
  const deadlineMs = Date.parse(deadlineAt);
  const hasDeadline = Boolean(deadlineAt) && Number.isFinite(deadlineMs);
  const ended = Boolean(summary?.closed) || (hasDeadline && clockMs >= deadlineMs);

  useEffect(() => {
    setClockMs(Date.now());
  }, [deadlineAt]);

  useEffect(() => {
    if (!hasDeadline || ended) return undefined;
    const timer = window.setTimeout(
      () => setClockMs(Date.now()),
      Math.min(1000, Math.max(50, deadlineMs - clockMs))
    );
    return () => window.clearTimeout(timer);
  }, [clockMs, deadlineMs, ended, hasDeadline]);

  async function castVote(option: string) {
    if (!canVote || ended || busyOption) return;
    setBusyOption(option);
    setError("");
    try {
      if (!roomSocket?.ready()) throw new Error("방 연결이 준비되지 않았습니다.");
      await roomSocket.say({
        message: "",
        kind: option === myChoice ? "vote_withdraw" : "vote_cast",
        voteId,
        ...(option === myChoice ? {} : { voteChoice: option }),
      });
      refresh();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "투표 실패");
    } finally {
      setBusyOption("");
    }
  }

  async function closeVote() {
    if (!canClose || ended || busyOption) return;
    setBusyOption("__close__");
    setError("");
    try {
      if (!roomSocket?.ready()) throw new Error("방 연결이 준비되지 않았습니다.");
      await roomSocket.say({
        message: "",
        kind: "vote_close",
        voteId,
      });
      refresh();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "투표 종료 실패");
    } finally {
      setBusyOption("");
    }
  }

  const options = summary?.options || event.vote_options || [];
  const question = summary?.question || event.vote_question || "";
  const total = summary?.total_votes ?? 0;
  const myChoice = summary?.own_choice || "";
  const deadlineLabel = ended
    ? "마감됨"
    : hasDeadline
      ? `남은 시간 ${remainingTimeLabel(deadlineMs - clockMs)}`
      : "마감 시간 없음";

  return (
    <section className="dc-vote-card" aria-label={`투표: ${question}`}>
      <header className="dc-vote-card-head">
        <BarChart3 size={15} aria-hidden />
        <span className="dc-vote-card-question preserve-words">{question}</span>
        {deadlineLabel && (
          <span
            className="shrink-0 rounded bg-black/15 px-2 py-1 text-[11px] font-bold text-text-muted"
          >
            {deadlineLabel}
          </span>
        )}
        {canClose && !ended && (
          <button
            type="button"
            className="dc-vote-close"
            onClick={() => void closeVote()}
            disabled={Boolean(busyOption)}
            aria-label="투표 종료"
            title="투표 종료"
          >
            종료
          </button>
        )}
        <button
          type="button"
          className="dc-vote-refresh"
          onClick={refresh}
          aria-label="투표 현황 새로고침"
          title="현황 새로고침"
        >
          <RefreshCw size={13} />
        </button>
      </header>
      <div className="dc-vote-options">
        {options.map((option) => {
          const count = summary?.tallies?.[option] ?? 0;
          const percent = total > 0 ? Math.round((count / total) * 100) : 0;
          return (
            <button
              key={option}
              type="button"
              className="dc-vote-option"
              data-mine={option === myChoice}
              disabled={!canVote || ended || Boolean(busyOption)}
              onClick={() => void castVote(option)}
              title={`${count}표`}
            >
              <span className="dc-vote-option-bar" style={{ width: `${percent}%` }} aria-hidden />
              <span className="dc-vote-option-label preserve-words">
                {option}
                {option === myChoice && <em className="dc-vote-mine-mark"> · 내 선택</em>}
              </span>
              <span className="dc-vote-option-count">
                {count}표{total > 0 ? ` · ${percent}%` : ""}
              </span>
            </button>
          );
        })}
      </div>
      <footer className="dc-vote-card-foot">
        <span>총 {total}명 참여{summary?.created_by ? ` · ${summary.created_by} 시작` : ""}</span>
        <span className="dc-vote-card-hint">
          {ended
            ? "투표가 마감되었습니다"
            : canVote
              ? "선택지를 누르면 투표 · 내 선택을 다시 누르면 철회"
              : "읽기 전용 세션은 투표할 수 없어요"}
        </span>
      </footer>
      {error && <p className="dc-vote-card-error preserve-words">{error}</p>}
    </section>
  );
}
