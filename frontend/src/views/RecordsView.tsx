import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Archive, FileText, Hash } from "lucide-react";
import {
  fetchMeetingDetail,
  fetchMeetings,
  type MeetingDetailResponse,
  type MeetingSummary,
} from "../api";
import { usePoll } from "../hooks";
import ChannelHeader from "./components/ChannelHeader";

export type ArchiveArtifactMap = Record<string, string | null | undefined>;

export type CanonicalArchiveArtifact = {
  path: string;
  label: string;
  description: string;
};

export type CanonicalArchiveArtifactRow = CanonicalArchiveArtifact & {
  available: boolean;
};

export const CANONICAL_FINAL_ARTIFACTS: CanonicalArchiveArtifact[] = [
  {
    path: "transcript.md",
    label: "진행 기록 / Transcript",
    description: "공식 발언으로 만든 회의록",
  },
  {
    path: "decision.md",
    label: "결정문 / Decision",
    description: "결정 게이트와 후속 판단",
  },
  {
    path: "shared_memory/rolling-summary.md",
    label: "공유 메모리: 요약",
    description: "공식 기록 기반 장기 맥락",
  },
  {
    path: "shared_memory/action-items.md",
    label: "공유 메모리: 실행 항목",
    description: "공식 기록에서 추출한 액션",
  },
  {
    path: "shared_memory/open-questions.md",
    label: "공유 메모리: 미해결 질문",
    description: "다음 회의가 이어받을 질문",
  },
];

const CANONICAL_FINAL_ARTIFACT_PATHS = new Set(
  CANONICAL_FINAL_ARTIFACTS.map((artifact) => artifact.path)
);

export function archiveArtifactHasContent(
  artifacts: ArchiveArtifactMap,
  path: string
): boolean {
  return Boolean(artifacts[path]);
}

export function canonicalArchiveArtifactRows(
  artifacts: ArchiveArtifactMap
): CanonicalArchiveArtifactRow[] {
  return CANONICAL_FINAL_ARTIFACTS.map((artifact) => ({
    ...artifact,
    available: archiveArtifactHasContent(artifacts, artifact.path),
  }));
}

export function availableArchiveArtifactNames(
  artifacts: ArchiveArtifactMap
): string[] {
  return Object.keys(artifacts).filter((path) =>
    archiveArtifactHasContent(artifacts, path)
  );
}

export function otherArchiveArtifactNames(artifacts: ArchiveArtifactMap): string[] {
  return availableArchiveArtifactNames(artifacts).filter(
    (path) => !CANONICAL_FINAL_ARTIFACT_PATHS.has(path)
  );
}

export function defaultArchiveArtifactSelection(
  artifacts: ArchiveArtifactMap,
  currentSelection?: string | null
): string | null {
  if (currentSelection && archiveArtifactHasContent(artifacts, currentSelection)) {
    return currentSelection;
  }
  const canonical = CANONICAL_FINAL_ARTIFACTS.find((artifact) =>
    archiveArtifactHasContent(artifacts, artifact.path)
  );
  if (canonical) return canonical.path;
  return otherArchiveArtifactNames(artifacts)[0] ?? null;
}

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
  return "border-line bg-panel-soft text-text-muted";
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
  if (meetings.length === 0) {
    return (
      <p className="px-3 py-2 text-[13px] text-text-muted preserve-words">
        기록된 회의가 없습니다.
      </p>
    );
  }
  return (
    <div className="space-y-0.5">
      {meetings.map((meeting) => {
        const selected = selectedId === meeting.meeting_id;
        return (
          <button
            key={meeting.meeting_id}
            type="button"
            onClick={() => onSelect(meeting.meeting_id)}
            data-active={selected}
            className="dc-channel flex-col items-start gap-0.5 py-2"
          >
            <span className="flex w-full items-center gap-1.5">
              <Hash size={14} className="shrink-0 opacity-60" />
              <span className="min-w-0 flex-1 truncate text-[13px] font-semibold preserve-words">
                {meeting.topic || meeting.meeting_id}
              </span>
              <span className="shrink-0 text-[10px] text-text-muted">{timeFromMtime(meeting.mtime)}</span>
            </span>
            <span className="truncate pl-5 text-[11px] text-text-muted preserve-words">
              {meeting.question || meeting.meeting_id}
            </span>
          </button>
        );
      })}
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

function artifactDisplayName(path: string): string {
  return path.replace("shared_memory/", "").replace(".md", "").replace(".json", "");
}

function ArchiveDetail({ detail }: { detail: MeetingDetailResponse | null }) {
  const [activeArtifact, setActiveArtifact] = useState<string | null>(null);
  const meeting = detail?.meeting ?? {};
  const artifacts = detail?.artifacts ?? {};
  const artifactNames = useMemo(() => otherArchiveArtifactNames(artifacts), [artifacts]);
  const canonicalArtifacts = useMemo(
    () => canonicalArchiveArtifactRows(artifacts),
    [artifacts]
  );
  const availableCanonicalCount = canonicalArtifacts.filter((artifact) => artifact.available).length;
  const meetingId = String(meeting.meeting_id || "");
  const previousMeetingIdRef = useRef(meetingId);

  useEffect(() => {
    const sameMeeting = previousMeetingIdRef.current === meetingId;
    const nextSelection = defaultArchiveArtifactSelection(
      artifacts,
      sameMeeting ? activeArtifact : null
    );
    previousMeetingIdRef.current = meetingId;
    if (nextSelection !== activeArtifact) {
      setActiveArtifact(nextSelection);
    }
  }, [activeArtifact, artifacts, meetingId]);

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div className="max-w-md">
          <Archive size={28} className="mx-auto mb-3 text-text-muted" />
          <h2 className="text-[16px] font-bold text-text-primary">기록을 선택하세요</h2>
          <p className="mt-2 text-[13px] text-text-muted preserve-words">
            왼쪽에서 세션을 선택하면 transcript, decision, shared memory를 확인할 수 있습니다.
          </p>
          <p className="mt-3 text-[13px] text-text-secondary preserve-words">
            회의가 없다면 채팅 채널에서 새 회의를 시작하세요.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 p-3 lg:p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 flex-1 truncate text-[20px] font-black preserve-words">
          {String(meeting.topic || meeting.meeting_id || "session")}
        </h1>
        {meeting.live_status && (
          <span className={`rounded-md border px-2.5 py-1 text-[12px] font-bold ${statusClass(String(meeting.live_status))}`}>
            {statusLabel(String(meeting.live_status))}
          </span>
        )}
      </div>
      <p className="text-[13px] text-text-secondary preserve-words">
        {String(meeting.question || "전략 방향 설정 및 역할 분담")}
      </p>

      <section className="ops-panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[15px] font-bold">최종 산출물 / Final artifacts</h2>
          <span className="rounded border border-line px-2 py-1 text-[11px] font-bold text-text-muted">
            {availableCanonicalCount}/{canonicalArtifacts.length} 생성
          </span>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {canonicalArtifacts.map((artifact) => (
            <button
              key={artifact.path}
              type="button"
              disabled={!artifact.available}
              onClick={() => artifact.available && setActiveArtifact(artifact.path)}
              className={`rounded-lg border p-3 text-left transition ${
                artifact.available
                  ? activeArtifact === artifact.path
                    ? "border-accent/70 bg-accent/12 text-text-primary"
                    : "border-line bg-panel-soft text-text-secondary hover:border-accent/45 hover:text-text-primary"
                  : "cursor-not-allowed border-line bg-panel/30 text-text-muted opacity-65"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[13px] font-bold preserve-words">{artifact.label}</p>
                  <p className="mt-1 text-[11px] preserve-words">{artifact.description}</p>
                </div>
                <span
                  className={`shrink-0 rounded border px-2 py-1 text-[10px] font-black ${
                    artifact.available
                      ? "border-online/30 bg-online/10 text-online"
                      : "border-line bg-panel/45 text-text-muted"
                  }`}
                >
                  {artifact.available ? "생성됨" : "미생성"}
                </span>
              </div>
              <p className="mt-2 truncate font-mono text-[10px] text-text-muted">{artifact.path}</p>
            </button>
          ))}
        </div>
      </section>

      {artifactNames.length > 0 && (
        <section className="ops-panel p-4">
          <h2 className="mb-3 text-[15px] font-bold">기타 산출물 / Other artifacts</h2>
          <div className="flex gap-2 overflow-x-auto pb-1 chat-scroll">
            {artifactNames.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => setActiveArtifact(name)}
                className={`shrink-0 rounded-md border px-3 py-2 text-[12px] font-bold transition-colors ${
                  activeArtifact === name
                    ? "border-accent/70 bg-accent/12 text-accent"
                    : "border-line bg-panel-soft text-text-muted hover:text-text-primary"
                }`}
              >
                {artifactDisplayName(name)}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="ops-panel p-4">
        <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold">
          <FileText size={17} className="text-accent" />
          핵심 결론
        </h2>
        {activeArtifact && artifacts[activeArtifact] ? (
          <ArtifactContent content={artifacts[activeArtifact]!} />
        ) : (
          <p className="text-[13px] text-text-muted preserve-words">
            아직 생성된 문서가 없습니다. 일반적으로 회의 최종화 후 transcript, decision, shared memory
            산출물이 생성됩니다.
          </p>
        )}
      </section>
    </div>
  );
}

export default function RecordsView() {
  const meetingsFetcher = useCallback(() => fetchMeetings(), []);
  const [data, loading] = usePoll<{ meetings: MeetingSummary[] }>(meetingsFetcher, 10000);
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
    <div className="flex h-full min-h-0 flex-col">
      <ChannelHeader
        icon={<Archive size={20} />}
        title="아카이브"
        subtitle="완료된 세션의 transcript · decision · shared memory"
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-60 shrink-0 flex-col border-r border-line bg-sidebar/40">
          <div className="dc-chat-head flex h-10 shrink-0 items-center justify-between px-3 text-[12px] font-bold text-text-muted">
            <span>세션 기록</span>
            <span>{loading && !data ? "조회 중" : `${meetings.length}개`}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2 chat-scroll">
            <MeetingList meetings={meetings} selectedId={selectedId} onSelect={handleSelect} />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto chat-scroll">
          {detailLoading ? (
            <p className="p-6 text-[14px] text-text-muted">불러오는 중...</p>
          ) : (
            <ArchiveDetail detail={detail} />
          )}
        </div>
      </div>
    </div>
  );
}
