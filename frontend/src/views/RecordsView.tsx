import { useCallback, useState } from "react";
import {
  fetchMeetingDetail,
  fetchMeetings,
  type MeetingDetailResponse,
  type MeetingSummary,
} from "../api";
import { usePoll } from "../hooks";
import { ChevronLeft } from "lucide-react";

function statusLabel(status: string): string {
  if (status === "active") return "진행 중";
  if (status === "complete") return "완료";
  if (status === "finalized") return "확정";
  return status || "알 수 없음";
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
      <div className="flex items-center justify-center h-full text-stone-400 text-sm">
        기록된 회의가 없습니다
      </div>
    );
  }

  return (
    <div className="py-1">
      {meetings.map((m) => (
        <button
          key={m.meeting_id}
          onClick={() => onSelect(m.meeting_id)}
          className="w-full text-left px-4 py-2.5 hover:bg-chat-hover transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="text-sm text-stone-700 truncate preserve-words flex-1">
              {m.topic || m.meeting_id}
            </span>
            <span className="text-[10px] text-stone-400 shrink-0">
              {statusLabel(m.live_status)}
            </span>
          </div>
          {m.question && (
            <p className="text-xs text-stone-400 mt-0.5 truncate preserve-words">
              {m.question}
            </p>
          )}
        </button>
      ))}
    </div>
  );
}

/** Render markdown-ish artifact content as readable prose, not raw pre. */
function ArtifactContent({ content }: { content: string }) {
  // Split into paragraphs, preserve line breaks within blocks
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let key = 0;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      elements.push(
        <h2 key={key++} className="text-base font-semibold text-stone-800 mt-4 mb-1 preserve-words">
          {line.slice(2)}
        </h2>
      );
    } else if (line.startsWith("## ")) {
      elements.push(
        <h3 key={key++} className="text-sm font-semibold text-stone-700 mt-3 mb-1 preserve-words">
          {line.slice(3)}
        </h3>
      );
    } else if (line.startsWith("### ")) {
      elements.push(
        <h4 key={key++} className="text-sm font-medium text-stone-600 mt-2 mb-0.5 preserve-words">
          {line.slice(4)}
        </h4>
      );
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      elements.push(
        <li key={key++} className="text-sm text-stone-600 ml-4 preserve-words leading-relaxed">
          {line.slice(2)}
        </li>
      );
    } else if (line.trim() === "") {
      elements.push(<div key={key++} className="h-2" />);
    } else {
      elements.push(
        <p key={key++} className="text-sm text-stone-600 preserve-words leading-relaxed">
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
  const artifactNames = Object.keys(artifacts).filter((k) => artifacts[k]);

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-stone-200 bg-white shrink-0">
        <button
          onClick={onBack}
          className="p-1 rounded hover:bg-stone-100 text-stone-400"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="text-sm font-semibold text-stone-700 truncate preserve-words">
          {meeting.topic || meeting.meeting_id || "회의"}
        </span>
        {meeting.live_status && (
          <span className="text-[10px] text-stone-400 shrink-0">
            {statusLabel(String(meeting.live_status))}
          </span>
        )}
      </div>

      {/* Artifact tabs */}
      {artifactNames.length > 0 && (
        <div className="flex gap-1 px-4 py-2 overflow-x-auto border-b border-stone-100 bg-white shrink-0">
          {artifactNames.map((name) => (
            <button
              key={name}
              onClick={() =>
                setActiveArtifact(activeArtifact === name ? null : name)
              }
              className={`text-xs px-2.5 py-1 rounded shrink-0 transition-colors ${
                activeArtifact === name
                  ? "bg-accent text-white"
                  : "bg-stone-100 text-stone-500 hover:bg-stone-200"
              }`}
            >
              {name.replace(".md", "").replace(".json", "")}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto chat-scroll px-4 py-4">
        {activeArtifact && artifacts[activeArtifact] ? (
          <ArtifactContent content={artifacts[activeArtifact]!} />
        ) : (
          <div className="space-y-3">
            {meeting.question && (
              <div>
                <div className="text-[11px] text-stone-400 mb-0.5">질문</div>
                <p className="text-sm text-stone-600 preserve-words leading-relaxed">
                  {String(meeting.question)}
                </p>
              </div>
            )}
            {artifactNames.length === 0 && (
              <p className="text-sm text-stone-400">
                아직 생성된 문서가 없습니다
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
      .then((d) => {
        setDetail(d);
        setDetailLoading(false);
      })
      .catch(() => setDetailLoading(false));
  }

  if ((loading && !data) || detailLoading) {
    return (
      <div className="flex items-center justify-center h-full text-stone-400 text-sm">
        불러오는 중…
      </div>
    );
  }

  if (detail) {
    return <MeetingDetailView detail={detail} onBack={() => setDetail(null)} />;
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center px-4 py-2.5 border-b border-stone-200 bg-white">
        <span className="text-sm font-semibold text-stone-700">기록</span>
        <span className="text-xs text-stone-400 ml-2">
          {meetings.length}건
        </span>
      </div>
      <div className="flex-1 overflow-y-auto chat-scroll">
        <MeetingList meetings={meetings} onSelect={handleSelect} />
      </div>
    </div>
  );
}
