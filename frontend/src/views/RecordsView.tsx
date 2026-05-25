import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  CalendarDays,
  ChevronLeft,
  Clock3,
  Download,
  FileDown,
  FileText,
  FolderTree,
  Search,
  Tag,
  Users,
} from "lucide-react";
import {
  fetchMeetingDetail,
  fetchMeetings,
  type LiveAgent,
  type MeetingDetailResponse,
  type MeetingSummary,
} from "../api";
import { usePoll } from "../hooks";

function statusLabel(status: string): string {
  if (status === "active") return "진행 중";
  if (status === "complete") return "완료";
  if (status === "finalized") return "확정";
  return status || "알 수 없음";
}

function statusClass(status: string) {
  if (status === "active") return "border-online/35 bg-online/10 text-online";
  if (status === "complete" || status === "finalized") {
    return "border-accent/35 bg-accent/10 text-accent";
  }
  return "border-text-muted/25 bg-panel-soft/50 text-text-muted";
}

function dateLabel(value?: string) {
  if (!value) return "--";
  try {
    return new Date(value).toLocaleDateString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return value;
  }
}

function timeFromMtime(value?: number) {
  if (!value) return "--";
  const diff = Date.now() - value * 1000;
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}시간 전`;
  return `${Math.floor(diff / 86_400_000)}일 전`;
}

function MeetingList({
  meetings,
  selectedId,
  onSelect,
}: {
  meetings: MeetingSummary[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      <label className="ops-input flex items-center gap-2 rounded-lg px-3 py-2.5 text-[13px] text-text-muted">
        <Search size={15} />
        <input
          readOnly
          value=""
          placeholder="세션 검색"
          className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-text-muted"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        {["전체", "Council", "Brainstorm", "War Room"].map((tag, index) => (
          <button
            key={tag}
            type="button"
            className={`rounded-md border px-2.5 py-1.5 text-[11px] font-bold ${
              index === 0
                ? "border-accent/60 bg-accent/12 text-accent"
                : "border-accent/16 bg-panel-soft/45 text-text-muted"
            }`}
          >
            {tag}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {meetings.length === 0 ? (
          <div className="ops-inner rounded-lg p-4 text-[13px] text-text-muted preserve-words">
            기록된 회의가 없습니다.
          </div>
        ) : (
          meetings.map((meeting) => {
            const selected = selectedId === meeting.meeting_id;
            return (
              <button
                key={meeting.meeting_id}
                type="button"
                onClick={() => onSelect(meeting.meeting_id)}
                className={`ops-inner flex w-full items-center gap-3 rounded-lg p-3 text-left transition-colors ${
                  selected ? "border-accent/80 shadow-[0_0_22px_rgba(34,211,238,0.16)]" : "hover:border-accent/40"
                }`}
              >
                <span className={selected ? "hex-badge h-10 w-10" : "hex-badge h-10 w-10 opacity-70"}>
                  <Archive size={16} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-black text-text-primary preserve-words">
                    {meeting.topic || meeting.meeting_id}
                  </p>
                  <p className="truncate text-[11px] text-text-muted preserve-words">
                    {meeting.question || meeting.meeting_id}
                  </p>
                </div>
                <div className="text-right text-[11px] text-text-muted">
                  <p>{timeFromMtime(meeting.mtime)}</p>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

function ArtifactContent({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: ReactNode[] = [];
  let key = 0;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      elements.push(
        <h2 key={key++} className="mb-1 mt-5 text-[18px] font-black text-text-primary preserve-words">
          {line.slice(2)}
        </h2>
      );
    } else if (line.startsWith("## ")) {
      elements.push(
        <h3 key={key++} className="mb-1 mt-4 text-[15px] font-black text-text-primary preserve-words">
          {line.slice(3)}
        </h3>
      );
    } else if (line.startsWith("### ")) {
      elements.push(
        <h4 key={key++} className="mb-0.5 mt-3 text-[13px] font-semibold text-text-secondary preserve-words">
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

function ArchiveDetail({
  detail,
  onBack,
}: {
  detail: MeetingDetailResponse | null;
  onBack: () => void;
}) {
  const [activeArtifact, setActiveArtifact] = useState<string | null>(null);
  const meeting = detail?.meeting ?? {};
  const artifacts = detail?.artifacts ?? {};
  const artifactNames = Object.keys(artifacts).filter((key) => artifacts[key]);

  useEffect(() => {
    if (!activeArtifact && artifactNames.length > 0) {
      setActiveArtifact(artifactNames[0]);
    }
  }, [activeArtifact, artifactNames]);

  if (!detail) {
    return (
      <section className="ops-panel ops-cut flex min-h-[620px] items-center justify-center p-8 text-center">
        <div>
          <span className="ops-logo-mark mx-auto mb-5 h-16 w-16" aria-hidden />
          <h1 className="text-[30px] font-black">아카이브</h1>
          <p className="mt-2 max-w-md text-[14px] text-text-muted preserve-words">
            왼쪽에서 세션을 선택하면 요약, 산출물, 하이라이트를 검토할 수 있습니다.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="ops-panel ops-cut min-h-[720px] overflow-hidden">
      <div className="ops-hero min-h-[210px] p-6">
        <div className="relative z-[1] flex flex-wrap items-start gap-5">
          <span className="ops-logo-mark h-16 w-16 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <button
              type="button"
              onClick={onBack}
              className="mb-3 flex items-center gap-1 text-[12px] font-bold text-text-muted hover:text-text-primary xl:hidden"
            >
              <ChevronLeft size={14} />
              목록
            </button>
            <h1 className="truncate text-[32px] font-black preserve-words">
              {String(meeting.topic || meeting.meeting_id || "session")}
            </h1>
            <p className="mt-2 text-[14px] text-text-secondary preserve-words">
              {String(meeting.question || "전략 방향 설정 및 역할 분담")}
            </p>
            <div className="mt-4 flex flex-wrap gap-3 text-[12px] text-text-muted">
              <span className="ops-chip flex items-center gap-2 rounded-md px-3 py-2">
                <CalendarDays size={14} />
                {dateLabel(String(meeting.created_at || ""))}
              </span>
              <span className="ops-chip flex items-center gap-2 rounded-md px-3 py-2">
                <Clock3 size={14} />
                {String(meeting.duration || "--")}
              </span>
              {meeting.live_status && (
                <span className={`rounded-md border px-3 py-2 text-[12px] font-bold ${statusClass(String(meeting.live_status))}`}>
                  {statusLabel(String(meeting.live_status))}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {artifactNames.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-b border-accent/14 px-4 py-3 chat-scroll">
          {artifactNames.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setActiveArtifact(name)}
              className={`shrink-0 rounded-md border px-3 py-2 text-[12px] font-bold transition-colors ${
                activeArtifact === name
                  ? "border-accent/70 bg-accent/12 text-accent"
                  : "border-accent/16 bg-panel-soft/45 text-text-muted hover:text-text-primary"
              }`}
            >
              {name.replace(".md", "").replace(".json", "")}
            </button>
          ))}
        </div>
      )}

      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_410px]">
        <div className="space-y-4">
          <section className="ops-inner rounded-lg p-4">
            <h2 className="mb-3 flex items-center gap-2 text-[17px] font-black">
              <FileText size={18} className="text-accent" />
              핵심 결론
            </h2>
            {activeArtifact && artifacts[activeArtifact] ? (
              <ArtifactContent content={artifacts[activeArtifact]!} />
            ) : (
              <p className="text-[13px] text-text-muted preserve-words">
                아직 생성된 문서가 없습니다.
              </p>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <section className="ops-inner rounded-lg p-4">
            <h2 className="mb-3 text-[15px] font-black">다음 단계</h2>
            <div className="space-y-2 text-[13px] text-text-secondary">
              {["공식 산출물 확인", "미결 질문 검토", "필요 시 Work Mode 승격"].map((item) => (
                <div key={item} className="flex items-center justify-between gap-3 border-b border-accent/10 py-2 last:border-b-0">
                  <span className="preserve-words">{item}</span>
                  <span className="text-text-muted">대기</span>
                </div>
              ))}
            </div>
          </section>

          <section className="ops-inner rounded-lg p-4">
            <h2 className="mb-3 text-[15px] font-black">하이라이트 발언</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
              {artifactNames.slice(0, 4).map((name, index) => (
                <div key={name} className="rounded-lg border border-accent/14 bg-black/18 p-3">
                  <div className="mb-2 flex items-center justify-between text-[11px] text-text-muted">
                    <span>#{index + 1}</span>
                    <span>{name.replace(".md", "")}</span>
                  </div>
                  <p className="line-clamp-3 text-[12px] leading-relaxed text-text-secondary preserve-words">
                    {artifacts[name]?.split("\n").find((line) => line.trim()) || "기록 요약"}
                  </p>
                  <div className="mt-3 h-8 rounded bg-accent/10">
                    <div className="h-full w-3/4 rounded bg-[linear-gradient(90deg,rgba(34,211,238,0.1),rgba(34,211,238,0.7),rgba(34,211,238,0.08))]" />
                  </div>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}

function ArchiveSide({ agents }: { agents: LiveAgent[] }) {
  const activeAgents = agents.filter((agent) => agent.status !== "offline");
  return (
    <aside className="space-y-4">
      <section className="ops-panel ops-cut p-4">
        <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
          <Users size={18} className="text-accent" />
          참가자 ({activeAgents.length})
        </h2>
        <div className="space-y-3">
          {activeAgents.slice(0, 6).map((agent) => (
            <div key={agent.agent_id} className="flex items-center gap-3">
              <span className="hex-badge h-9 w-9">
                <Users size={14} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-bold preserve-words">
                  {agent.display_name || agent.agent_id}
                </p>
                <p className="truncate text-[11px] text-text-muted preserve-words">
                  {agent.provider_kind || agent.engagement_mode || "resident"}
                </p>
              </div>
              <span className="h-2.5 w-2.5 rounded-full bg-online" />
            </div>
          ))}
          {activeAgents.length === 0 && (
            <p className="text-[13px] text-text-muted">표시할 참가자가 없습니다.</p>
          )}
        </div>
      </section>

      <section className="ops-panel ops-cut p-4">
        <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
          <Tag size={18} className="text-accent" />
          태그
        </h2>
        <div className="flex flex-wrap gap-2">
          {["전략", "역할분담", "Play Mode", "로컬", "기록경계", "+"].map((tag) => (
            <span key={tag} className="ops-chip rounded-md px-3 py-2 text-[12px] font-bold">
              {tag}
            </span>
          ))}
        </div>
      </section>

      <section className="ops-panel ops-cut p-4">
        <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
          <Download size={18} className="text-accent" />
          내보내기 / 다운로드
        </h2>
        <div className="grid grid-cols-2 gap-3">
          {["요약 리포트", "전체 기록", "하이라이트", "결정사항"].map((label) => (
            <button key={label} type="button" disabled className="ops-button rounded-lg px-3 py-3 text-[12px] font-bold">
              <FileDown className="mx-auto mb-2 text-text-secondary" size={17} />
              {label}
            </button>
          ))}
        </div>
      </section>
    </aside>
  );
}

export default function RecordsView({ agents }: { agents: LiveAgent[] }) {
  const meetingsFetcher = useCallback(() => fetchMeetings(), []);
  const [data, loading] = usePoll<{ meetings: MeetingSummary[] }>(
    meetingsFetcher,
    10000
  );
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [detail, setDetail] = useState<MeetingDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const meetings = useMemo(
    () => (Array.isArray(data?.meetings) ? data.meetings : []),
    [data?.meetings]
  );

  useEffect(() => {
    if (selectedId || meetings.length === 0) return;
    const firstId = meetings[0].meeting_id;
    setSelectedId(firstId);
    setDetailLoading(true);
    fetchMeetingDetail(firstId)
      .then((nextDetail) => {
        setDetail(nextDetail);
        setDetailLoading(false);
      })
      .catch(() => setDetailLoading(false));
  }, [meetings, selectedId]);

  function handleSelect(id: string) {
    setSelectedId(id);
    setDetailLoading(true);
    fetchMeetingDetail(id)
      .then((nextDetail) => {
        setDetail(nextDetail);
        setDetailLoading(false);
      })
      .catch(() => setDetailLoading(false));
  }

  return (
    <div className="grid min-h-full gap-4 xl:grid-cols-[350px_minmax(0,1fr)_340px]">
      <aside className="space-y-4">
        <section className="ops-panel ops-cut p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-[17px] font-black">
              <Archive size={18} className="text-accent" />
              세션 기록
            </h2>
            <span className="text-[12px] font-bold text-text-muted">
              {loading && !data ? "조회 중" : `${meetings.length}개`}
            </span>
          </div>
          <MeetingList meetings={meetings} selectedId={selectedId} onSelect={handleSelect} />
        </section>

        <section className="ops-panel ops-cut p-4">
          <h2 className="mb-4 flex items-center gap-2 text-[17px] font-black">
            <FolderTree size={18} className="text-accent" />
            아카이브 트리
          </h2>
          <div className="ops-inner rounded-lg p-4 text-[13px] text-text-secondary">
            <p className="font-bold text-text-primary">2026</p>
            <p className="mt-2 pl-4 text-text-muted">05. May</p>
            <p className="mt-2 rounded border border-accent/20 bg-accent/8 px-3 py-2 preserve-words">
              {selectedId || meetings[0]?.meeting_id || "세션을 선택하세요"}
            </p>
          </div>
        </section>
      </aside>

      {detailLoading ? (
        <section className="ops-panel ops-cut flex min-h-[620px] items-center justify-center text-[14px] text-text-muted">
          불러오는 중...
        </section>
      ) : (
        <ArchiveDetail detail={detail} onBack={() => setDetail(null)} />
      )}

      <ArchiveSide agents={agents} />
    </div>
  );
}
