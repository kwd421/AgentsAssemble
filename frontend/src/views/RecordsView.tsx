import { useCallback, useState } from "react";
import {
  fetchMeetingDetail,
  fetchMeetings,
  type MeetingDetailResponse,
  type MeetingSummary,
} from "../api";
import { usePoll } from "../hooks";
import { Archive, ChevronLeft, FileText } from "lucide-react";

function statusLabel(status: string): string {
  if (status === "active") return "진행 중";
  if (status === "complete") return "완료";
  if (status === "finalized") return "확정";
  return status || "알 수 없음";
}

function statusClass(status: string) {
  if (status === "active") return "bg-online/15 text-online";
  if (status === "complete" || status === "finalized") {
    return "bg-accent/15 text-[#b8c0ff]";
  }
  return "bg-panel-soft text-text-muted";
}

function MeetingList({
  meetings,
  onSelect,
}: {
  meetings: MeetingSummary[];
  onSelect: (id: string) => void;
}) {
  if (meetings.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-panel-soft text-text-muted">
          <Archive size={24} />
        </div>
        <p className="text-[14px] font-semibold text-text-secondary">
          기록된 회의가 없습니다
        </p>
        <p className="mt-1 text-[12px] text-text-muted preserve-words">
          Play Mode나 공식 회의가 끝나면 여기에 쌓입니다.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2 p-3">
      {meetings.map((meeting) => (
        <button
          key={meeting.meeting_id}
          onClick={() => onSelect(meeting.meeting_id)}
          className="w-full rounded-lg border border-panel-border bg-panel-soft/45 px-3.5 py-3 text-left transition-colors hover:bg-panel-soft"
        >
          <div className="flex items-center gap-2.5">
            <FileText size={16} className="shrink-0 text-text-muted" />
            <span className="min-w-0 flex-1 truncate text-[14px] font-bold text-text-primary preserve-words">
              {meeting.topic || meeting.meeting_id}
            </span>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${statusClass(meeting.live_status)}`}
            >
              {statusLabel(meeting.live_status)}
            </span>
          </div>
          {meeting.question && (
            <p className="ml-[26px] mt-1 truncate text-[12px] text-text-muted preserve-words">
              {meeting.question}
            </p>
          )}
        </button>
      ))}
    </div>
  );
}

function ArtifactContent({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let key = 0;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      elements.push(
        <h2 key={key++} className="mt-5 mb-1 text-[16px] font-bold text-text-primary preserve-words">
          {line.slice(2)}
        </h2>
      );
    } else if (line.startsWith("## ")) {
      elements.push(
        <h3 key={key++} className="mt-4 mb-1 text-[14px] font-bold text-text-primary preserve-words">
          {line.slice(3)}
        </h3>
      );
    } else if (line.startsWith("### ")) {
      elements.push(
        <h4 key={key++} className="mt-3 mb-0.5 text-[13px] font-semibold text-text-secondary preserve-words">
          {line.slice(4)}
        </h4>
      );
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      elements.push(
        <li key={key++} className="ml-4 text-[13px] leading-relaxed text-text-secondary preserve-words">
          {line.slice(2)}
        </li>
      );
    } else if (line.trim() === "") {
      elements.push(<div key={key++} className="h-2" />);
    } else {
      elements.push(
        <p key={key++} className="text-[13px] leading-relaxed text-text-secondary preserve-words">
          {line}
        </p>
      );
    }
  }

  return <div className="space-y-0.5">{elements}</div>;
}

function MeetingDetailView({
  detail,
  onBack,
}: {
  detail: MeetingDetailResponse;
  onBack: () => void;
}) {
  const [activeArtifact, setActiveArtifact] = useState<string | null>(null);
  const meeting = detail.meeting ?? {};
  const artifacts = detail.artifacts ?? {};
  const artifactNames = Object.keys(artifacts).filter((key) => artifacts[key]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-chat-bg">
      <div className="flex shrink-0 items-center gap-2 border-b border-black/20 px-4 py-3">
        <button
          onClick={onBack}
          className="rounded-md p-1.5 text-text-muted hover:bg-panel-soft hover:text-text-primary"
        >
          <ChevronLeft size={17} />
        </button>
        <span className="min-w-0 flex-1 truncate text-[14px] font-bold text-text-primary preserve-words">
          {meeting.topic || meeting.meeting_id || "회의"}
        </span>
        {meeting.live_status && (
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${statusClass(String(meeting.live_status))}`}
          >
            {statusLabel(String(meeting.live_status))}
          </span>
        )}
      </div>

      {artifactNames.length > 0 && (
        <div className="flex shrink-0 gap-1.5 overflow-x-auto border-b border-black/20 bg-panel-bg/45 px-4 py-2.5">
          {artifactNames.map((name) => (
            <button
              key={name}
              onClick={() => setActiveArtifact(activeArtifact === name ? null : name)}
              className={`shrink-0 rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors ${
                activeArtifact === name
                  ? "bg-accent text-white"
                  : "bg-panel-soft text-text-muted hover:text-text-primary"
              }`}
            >
              {name.replace(".md", "").replace(".json", "")}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-5 py-5 chat-scroll">
        {activeArtifact && artifacts[activeArtifact] ? (
          <ArtifactContent content={artifacts[activeArtifact]!} />
        ) : (
          <div className="space-y-4">
            {meeting.question && (
              <div className="rounded-lg border border-panel-border bg-panel-soft/40 p-4">
                <div className="mb-1 text-[11px] font-bold uppercase tracking-wider text-text-muted">
                  질문
                </div>
                <p className="text-[13px] leading-relaxed text-text-secondary preserve-words">
                  {String(meeting.question)}
                </p>
              </div>
            )}
            {artifactNames.length === 0 ? (
              <p className="text-[13px] text-text-muted">
                아직 생성된 문서가 없습니다
              </p>
            ) : (
              <p className="text-[13px] text-text-muted">
                위 탭에서 문서를 선택하세요.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function RecordsView() {
  const meetingsFetcher = useCallback(() => fetchMeetings(), []);
  const [data, loading] = usePoll<{ meetings: MeetingSummary[] }>(
    meetingsFetcher,
    10000
  );
  const [detail, setDetail] = useState<MeetingDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const meetings = Array.isArray(data?.meetings) ? data.meetings : [];

  function handleSelect(id: string) {
    setDetailLoading(true);
    fetchMeetingDetail(id)
      .then((nextDetail) => {
        setDetail(nextDetail);
        setDetailLoading(false);
      })
      .catch(() => setDetailLoading(false));
  }

  if ((loading && !data) || detailLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-text-muted">
        불러오는 중…
      </div>
    );
  }

  if (detail) {
    return <MeetingDetailView detail={detail} onBack={() => setDetail(null)} />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-chat-bg">
      <div className="flex shrink-0 items-center gap-2 border-b border-black/20 px-4 py-3">
        <Archive size={16} className="text-text-muted" />
        <span className="text-[14px] font-bold text-text-primary">기록</span>
        <span className="rounded-full bg-panel-soft px-2 py-0.5 text-[11px] font-semibold text-text-muted">
          {meetings.length}건
        </span>
      </div>
      <div className="flex-1 overflow-y-auto chat-scroll">
        <MeetingList meetings={meetings} onSelect={handleSelect} />
      </div>
    </div>
  );
}
