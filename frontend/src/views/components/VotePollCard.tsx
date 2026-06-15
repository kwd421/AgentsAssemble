import { useCallback, useEffect, useState } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
import {
  fetchLobbyVote,
  fetchRoomVote,
  postLobbyMessage,
  postRoomSay,
  type LobbyEvent,
  type VoteSummary,
} from "../../api";

export default function VotePollCard({
  event,
  meetingId,
  roomSessionToken = "",
  voterName,
  canVote = true,
}: {
  event: LobbyEvent;
  meetingId: string;
  roomSessionToken?: string;
  voterName: string;
  canVote?: boolean;
}) {
  const voteId = event.vote_id || event.id;
  const [summary, setSummary] = useState<VoteSummary | null>(null);
  const [busyOption, setBusyOption] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    const request = roomSessionToken
      ? fetchRoomVote(roomSessionToken, voteId)
      : fetchLobbyVote(meetingId, voteId);
    request
      .then((payload) => {
        setSummary(payload);
        setError("");
      })
      .catch((errorValue) => {
        setError(errorValue instanceof Error ? errorValue.message : "투표 현황을 불러오지 못했습니다.");
      });
  }, [meetingId, roomSessionToken, voteId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function castVote(option: string) {
    if (!canVote || busyOption) return;
    setBusyOption(option);
    setError("");
    try {
      if (roomSessionToken) {
        await postRoomSay({
          sessionToken: roomSessionToken,
          message: "",
          kind: "vote_cast",
          voteId,
          voteChoice: option,
        });
      } else {
        await postLobbyMessage({
          name: voterName,
          side: "mine",
          kind: "vote_cast",
          message: "",
          meetingId,
          voteId,
          voteChoice: option,
        });
      }
      refresh();
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "투표 실패");
    } finally {
      setBusyOption("");
    }
  }

  const options = summary?.options || event.vote_options || [];
  const question = summary?.question || event.vote_question || "";
  const total = summary?.total_votes ?? 0;
  const myChoice = options.find((option) => (summary?.voters?.[option] || []).includes(voterName));

  return (
    <section className="dc-vote-card" aria-label={`투표: ${question}`}>
      <header className="dc-vote-card-head">
        <BarChart3 size={15} aria-hidden />
        <span className="dc-vote-card-question preserve-words">{question}</span>
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
          const voters = summary?.voters?.[option] || [];
          return (
            <button
              key={option}
              type="button"
              className="dc-vote-option"
              data-mine={option === myChoice}
              disabled={!canVote || Boolean(busyOption)}
              onClick={() => void castVote(option)}
              title={voters.length ? `투표: ${voters.join(", ")}` : "아직 아무도 투표하지 않음"}
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
          {canVote ? "선택지를 누르면 투표 (다시 누르면 변경)" : "읽기 전용 세션은 투표할 수 없어요"}
        </span>
      </footer>
      {error && <p className="dc-vote-card-error preserve-words">{error}</p>}
    </section>
  );
}
