import { useCallback } from "react";
import { Activity, ClipboardCheck, Cpu, Shield, Terminal, X } from "lucide-react";
import {
  fetchHealth,
  fetchLocalResources,
  fetchReleaseHealth,
  fetchReleaseHealthQueue,
  type HealthStatus,
  type ReleaseHealthCheck,
  type ReleaseHealthQueueCheck,
  type LocalResourceStatus,
  type ReleaseHealthCatalog,
  type ReleaseHealthQueue,
} from "../api";
import { usePoll } from "../hooks";
import {
  formatLoadAverageTriple,
  formatResourceMemory,
  localResourceSpotlightRows,
  resourceAttentionLabel,
  resourceRoleLabel,
} from "../lib/localResourceLabels";
import {
  partitionReleaseHealthChecks,
  releaseHealthLatestById,
  releaseHealthQueueBadge,
  releaseHealthBenchmarkRows,
  releaseHealthSafetyLabel,
  releaseHealthSelector,
  releaseHealthStatusLabel,
  releaseHealthStatusTone,
} from "../lib/releaseHealthLabels";

function formatSnapshotAge(generatedAt?: string) {
  if (!generatedAt) {
    return "미확인";
  }
  const timestamp = new Date(generatedAt).getTime();
  if (!Number.isFinite(timestamp)) {
    return "미확인";
  }
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) {
    return `${seconds}초 전`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}분 전`;
}

function releaseHealthStatusClass(status?: string) {
  const tone = releaseHealthStatusTone(status);
  if (tone === "online") return "border-online/25 bg-online/10 text-online";
  if (tone === "danger") return "border-offline/35 bg-offline/10 text-offline";
  if (tone === "warn") return "border-idle/35 bg-idle/10 text-idle";
  return "border-line/60 bg-black/18 text-text-muted";
}

function formatCheckDuration(seconds?: number | null) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(1)}s`;
}

function ReleaseHealthCard({
  check,
  latest,
}: {
  check: ReleaseHealthCheck;
  latest?: ReleaseHealthQueueCheck;
}) {
  const badge = releaseHealthQueueBadge(check);
  const latestStatus = latest?.latest_status || "not_run";
  const duration = formatCheckDuration(latest?.latest_duration_seconds);
  const benchmarkRows = releaseHealthBenchmarkRows(latest?.benchmark_summary);
  return (
    <div className="ops-inner rounded-lg px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 truncate text-[13px] font-black text-text-primary preserve-words">
          {check.order ? <span className="mr-2 text-accent">{check.order}</span> : null}
          {check.label}
        </p>
        <span className="shrink-0 rounded border border-line/60 px-2 py-0.5 text-[10px] font-bold text-text-muted">
          {check.kind}
        </span>
        <span
          className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-bold ${
            badge === "default"
              ? "border-accent/25 bg-accent/8 text-accent"
              : "border-idle/30 bg-idle/10 text-idle"
          }`}
        >
          {badge}
        </span>
        <span
          className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-bold ${releaseHealthStatusClass(latestStatus)}`}
        >
          {releaseHealthStatusLabel(latestStatus)}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-text-muted preserve-words">
        {check.category} · {releaseHealthSafetyLabel(check.safety_class)} · {check.requires.join(", ")}
      </p>
      {latest && latest.latest_status !== "not_run" && (
        <p className="mt-1 text-[11px] text-text-muted preserve-words">
          최근 결과 {releaseHealthStatusLabel(latest.latest_status)}
          {duration ? ` · ${duration}` : ""}
          {latest.skipped_reason ? ` · ${latest.skipped_reason}` : ""}
        </p>
      )}
      {benchmarkRows.length > 0 && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {benchmarkRows.map((row) => (
            <div key={row.id} className="rounded-md border border-line/60 bg-black/16 px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 text-[10px] font-bold text-text-muted preserve-words">
                  {row.label}
                </span>
                <span
                  className={`shrink-0 text-[10px] font-black ${
                    row.ok === false ? "text-offline" : row.ok === true ? "text-online" : "text-text-secondary"
                  }`}
                >
                  {row.ok === false ? "주의" : row.ok === true ? "정상" : "참고"}
                </span>
              </div>
              <p className="mt-1 text-[15px] font-black text-text-primary">{row.value}</p>
              <p className="text-[10px] text-text-muted preserve-words">{row.detail}</p>
            </div>
          ))}
        </div>
      )}
      <p className="mt-2 rounded-md border border-line/60 bg-black/18 px-3 py-2 font-mono text-[11px] text-text-secondary preserve-words">
        {releaseHealthSelector(check)}
      </p>
    </div>
  );
}

export default function AdminPanel({ onClose }: { onClose: () => void }) {
  const healthFetcher = useCallback(() => fetchHealth(), []);
  const resourcesFetcher = useCallback(() => fetchLocalResources(), []);
  const releaseHealthFetcher = useCallback(() => fetchReleaseHealth(), []);
  const releaseHealthQueueFetcher = useCallback(() => fetchReleaseHealthQueue(), []);
  const [health] = usePoll<HealthStatus>(healthFetcher, 8000);
  const [resources, resourcesLoading, resourcesError] = usePoll<LocalResourceStatus>(resourcesFetcher, 8000);
  const [releaseHealth] = usePoll<ReleaseHealthCatalog>(releaseHealthFetcher, 30000);
  const [releaseHealthQueue] = usePoll<ReleaseHealthQueue>(releaseHealthQueueFetcher, 30000);

  const agents = health?.agents;
  const sharedMemory = health?.shared_memory;
  const ok = health?.status === "ok";
  const resourceOk = resources?.status === "ok";
  const resourceRoleRows = resources?.summary.role_breakdown
    ? Object.entries(resources.summary.role_breakdown)
    : [];
  const resourceSpotlightRows = localResourceSpotlightRows(resources?.processes ?? []);
  const {
    defaultChecks: releaseHealthDefaultChecks,
    optInChecks: releaseHealthOptInChecks,
  } = partitionReleaseHealthChecks(releaseHealth);
  const latestStatusById = releaseHealthLatestById(releaseHealthQueue);

  return (
    <div className="ops-panel ops-cut mx-auto flex min-h-full max-w-5xl flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between border-b border-accent/14 px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="hex-badge">
            <Shield size={17} />
          </span>
          <div>
            <h1 className="text-[20px] font-black">관리</h1>
            <p className="text-[12px] text-text-muted">read-only operator state</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ops-button grid h-10 w-10 place-items-center rounded-lg"
          aria-label="관리 닫기"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5 chat-scroll">
        <section className="ops-inner rounded-xl p-5">
          <div className="mb-4 flex items-center gap-2">
            <Activity size={17} className={ok ? "text-online" : "text-idle"} />
            <h2 className="text-[15px] font-black">시스템 상태</h2>
          </div>
          {health ? (
            <div className="space-y-4">
              <span
                className={`inline-flex rounded-md border px-3 py-1.5 text-[12px] font-black ${
                  ok
                    ? "border-online/35 bg-online/10 text-online"
                    : "border-idle/35 bg-idle/10 text-idle"
                }`}
              >
                {ok ? "정상" : "주의"}
              </span>
              {agents && (
                <div className="grid gap-3 text-[13px] text-text-secondary sm:grid-cols-2 lg:grid-cols-3">
                  <div className="ops-inner rounded-lg px-4 py-3">
                    활성 에이전트{" "}
                    <span className="font-black text-text-primary">
                      {agents.live ?? 0}/{agents.total ?? 0}
                    </span>
                  </div>
                  {agents.counts &&
                    Object.entries(agents.counts)
                      .filter(([, value]) => value > 0)
                      .slice(0, 5)
                      .map(([key, value]) => (
                        <div key={key} className="ops-inner rounded-lg px-4 py-3">
                          {key}{" "}
                          <span className="font-black text-text-primary">{value}</span>
                        </div>
                      ))}
                </div>
              )}
              {agents?.attention && agents.attention.length > 0 && (
                <p className="rounded-lg border border-idle/25 bg-idle/10 px-4 py-3 text-[13px] font-semibold text-idle preserve-words">
                  주의: {agents.attention.slice(0, 3).map(resourceAttentionLabel).join(", ")}
                  {agents.attention.length > 3 &&
                    ` 외 ${agents.attention.length - 3}건`}
                </p>
              )}
              {sharedMemory && (
                <div className="rounded-lg border border-accent/18 bg-panel/35 px-4 py-3">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-[13px] font-black text-text-primary">공유 메모리</h3>
                    <span className="rounded border border-line/60 bg-black/18 px-2 py-1 text-[10px] font-black text-text-muted">
                      공식 메모리 카운트만 표시 · 본문 비표시
                    </span>
                  </div>
                  <div className="grid gap-2 text-[12px] text-text-secondary sm:grid-cols-2 lg:grid-cols-4">
                    <div className="ops-inner rounded-lg px-3 py-2">
                      메모리 보유{" "}
                      <span className="font-black text-text-primary">
                        {sharedMemory.with_memory}/{sharedMemory.ready_sessions}
                      </span>
                    </div>
                    <div className="ops-inner rounded-lg px-3 py-2">
                      공식 이벤트{" "}
                      <span className="font-black text-text-primary">
                        {sharedMemory.official_event_count}
                      </span>
                    </div>
                    <div className="ops-inner rounded-lg px-3 py-2">
                      열린 질문{" "}
                      <span className="font-black text-text-primary">
                        {sharedMemory.open_question_count}
                      </span>
                    </div>
                    <div className="ops-inner rounded-lg px-3 py-2">
                      액션 아이템{" "}
                      <span className="font-black text-text-primary">
                        {sharedMemory.action_item_count}
                      </span>
                    </div>
                  </div>
                  {sharedMemory.attention && sharedMemory.attention.length > 0 && (
                    <p className="mt-3 rounded-md border border-idle/25 bg-idle/10 px-3 py-2 text-[12px] font-semibold text-idle preserve-words">
                      메모리 주의: {sharedMemory.attention.slice(0, 3).join(", ")}
                      {sharedMemory.attention.length > 3 &&
                        ` 외 ${sharedMemory.attention.length - 3}건`}
                    </p>
                  )}
                </div>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">연결 확인 중...</p>
          )}
        </section>

        <section className="ops-inner rounded-xl p-5">
          <div className="mb-4 flex items-center gap-2">
            <Cpu size={17} className={resourceOk ? "text-online" : "text-idle"} />
            <h2 className="text-[15px] font-black">로컬 리소스</h2>
          </div>
          <div className="mb-4 space-y-2 rounded-lg border border-line/60 bg-panel/35 px-4 py-3 text-[12px] text-text-secondary">
            <p className="font-semibold text-text-primary preserve-words">
              조회 전용 · 프로세스 기본명만 표시 · 인자/경로/세션 비표시
            </p>
            <p className="preserve-words">
              감독 중 = 감독 그룹 PID · AA 자식 = 본 프로세스/자식 · 기타 = 화이트리스트 매칭
            </p>
          </div>
          {resources ? (
            <div className="space-y-4">
              <div className="grid gap-3 text-[13px] text-text-secondary sm:grid-cols-2 lg:grid-cols-4">
                <div className="ops-inner rounded-lg px-4 py-3">
                  상태{" "}
                  <span className={`font-black ${resourceOk ? "text-online" : "text-idle"}`}>
                    {resourceOk ? "정상" : resources.status}
                  </span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  Load{" "}
                  <span className="block font-black text-text-primary">
                    {formatLoadAverageTriple(resources.load_average)}
                  </span>
                  <span className="text-[11px] text-text-muted">1분 · 5분 · 15분</span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  CPU{" "}
                  <span className="font-black text-text-primary">{resources.cpu_count}</span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  추적 프로세스{" "}
                  <span className="font-black text-text-primary">
                    {resources.summary.process_count}
                  </span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  표시 CPU 합계{" "}
                  <span className="font-black text-text-primary">
                    {resources.summary.total_cpu_pct.toFixed(1)}%
                  </span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  표시 RSS 합계{" "}
                  <span className="font-black text-text-primary">
                    {formatResourceMemory(resources.summary.total_rss_kb)}
                  </span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  감독 중{" "}
                  <span className="font-black text-text-primary">
                    {resources.summary.supervised_resident_count}/{resources.summary.process_count}
                  </span>
                </div>
                <div className="ops-inner rounded-lg px-4 py-3">
                  스냅샷{" "}
                  <span className="font-black text-text-primary">
                    {formatSnapshotAge(resources.generated_at)}
                  </span>
                </div>
              </div>
              {resources.summary.attention.length > 0 && (
                <p className="rounded-lg border border-idle/25 bg-idle/10 px-4 py-3 text-[13px] font-semibold text-idle preserve-words">
                  주의: {resources.summary.attention.slice(0, 3).map(resourceAttentionLabel).join(", ")}
                  {resources.summary.attention.length > 3 &&
                    ` 외 ${resources.summary.attention.length - 3}건`}
                </p>
              )}
              {resourceSpotlightRows.length > 0 && (
                <div className="grid gap-3 sm:grid-cols-2">
                  {resourceSpotlightRows.map((row) => (
                    <div key={row.id} className="ops-inner rounded-lg px-4 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-[11px] font-bold uppercase tracking-wider text-text-muted">
                            {row.label}
                          </p>
                          <p className="mt-1 truncate text-[15px] font-black text-text-primary preserve-words">
                            {row.processName}
                          </p>
                        </div>
                        <span className="shrink-0 text-[18px] font-black text-accent">
                          {row.value}
                        </span>
                      </div>
                      <p className="mt-2 text-[11px] text-text-muted preserve-words">
                        {row.roleLabel} · {row.detail}
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {resourceRoleRows.length > 0 && (
                <div className="flex flex-wrap gap-2 text-[12px] text-text-secondary">
                  {resourceRoleRows.map(([role, row]) => (
                    <span key={role} className="rounded-lg border border-line/60 bg-panel/35 px-3 py-2 preserve-words">
                      <span className="font-black text-text-primary">
                        {resourceRoleLabel(role)}
                      </span>{" "}
                      {row.count}개 · {row.cpu_pct.toFixed(1)}% · {formatResourceMemory(row.rss_kb)}
                    </span>
                  ))}
                </div>
              )}
              <div className="overflow-hidden rounded-lg border border-line/60">
                <div className="grid grid-cols-[64px_64px_minmax(0,1fr)_88px_96px] gap-2 border-b border-line/60 bg-panel/45 px-3 py-2 text-[11px] font-black uppercase text-text-muted">
                  <span>PID</span>
                  <span>PPID</span>
                  <span>프로세스</span>
                  <span>CPU</span>
                  <span>RSS</span>
                </div>
                {resources.processes.slice(0, 8).map((process) => (
                  <div
                    key={`${process.pid}-${process.comm}`}
                    className="grid grid-cols-[64px_64px_minmax(0,1fr)_88px_96px] gap-2 border-b border-line/35 px-3 py-2 text-[12px] text-text-secondary last:border-b-0"
                  >
                    <span className="font-mono text-text-muted">{process.pid}</span>
                    <span className="font-mono text-text-muted">{process.ppid}</span>
                    <span className="min-w-0">
                      <span className="font-bold text-text-primary preserve-words">{process.comm}</span>
                      <span className="ml-2 text-text-muted">{resourceRoleLabel(process.role)}</span>
                    </span>
                    <span>{process.cpu_pct.toFixed(1)}%</span>
                    <span>{formatResourceMemory(process.rss_kb)}</span>
                  </div>
                ))}
                {resources.processes.length === 0 && (
                  <p className="px-3 py-3 text-[13px] text-text-muted">표시할 로컬 프로세스가 없습니다.</p>
                )}
              </div>
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">
              {resourcesError
                ? "로컬 리소스 정보를 읽지 못했습니다."
                : resourcesLoading
                  ? "리소스 확인 중..."
                  : "리소스 정보가 없습니다."}
            </p>
          )}
        </section>

        <section className="ops-inner rounded-xl p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ClipboardCheck size={17} className="text-accent" />
              <h2 className="text-[15px] font-black">릴리스 헬스</h2>
            </div>
            <span className="rounded-md border border-accent/25 bg-accent/10 px-2.5 py-1 text-[11px] font-black text-accent">
              CLI-only
            </span>
          </div>
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-line/60 bg-panel/35 px-4 py-3 text-[12px] text-text-secondary">
            <Terminal size={15} className="mt-0.5 shrink-0 text-text-muted" />
            <span className="min-w-0 preserve-words">
              실행은 터미널에서 <code className="font-mono text-text-primary">assemble release-health run</code>
            </span>
          </div>
          {releaseHealthQueue?.source?.has_latest_run && (
            <div className="mb-4 grid gap-2 text-[12px] text-text-secondary sm:grid-cols-3">
              <div className="ops-inner rounded-lg px-3 py-2">
                최근 상태{" "}
                <span className="font-black text-text-primary">
                  {releaseHealthStatusLabel(releaseHealthQueue.source.latest_status)}
                </span>
              </div>
              <div className="ops-inner rounded-lg px-3 py-2">
                통과/실패{" "}
                <span className="font-black text-text-primary">
                  {releaseHealthQueue.summary.latest_passed}/{releaseHealthQueue.summary.latest_failed}
                </span>
              </div>
              <div className="ops-inner rounded-lg px-3 py-2">
                최근 실행{" "}
                <span className="font-black text-text-primary">
                  {formatSnapshotAge(releaseHealthQueue.source.latest_completed_at)}
                </span>
              </div>
            </div>
          )}
          {releaseHealth ? (
            <div className="space-y-4">
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <h3 className="text-[13px] font-black text-text-primary">기본 프루프 큐</h3>
                  <span className="text-[11px] font-bold text-text-muted">
                    {releaseHealthDefaultChecks.length} checks
                  </span>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {releaseHealthDefaultChecks.map((check) => (
                    <ReleaseHealthCard
                      key={check.id}
                      check={check}
                      latest={latestStatusById.get(check.id)}
                    />
                  ))}
                </div>
              </div>

              {releaseHealthOptInChecks.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <h3 className="text-[13px] font-black text-text-primary">선택 검사</h3>
                    <span className="text-[11px] font-bold text-text-muted">옵트인</span>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {releaseHealthOptInChecks.map((check) => (
                      <ReleaseHealthCard
                        key={check.id}
                        check={check}
                        latest={latestStatusById.get(check.id)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">릴리스 헬스 카탈로그 확인 중...</p>
          )}
        </section>

        <section className="ops-inner rounded-xl p-5">
          <h2 className="mb-3 text-[15px] font-black">참고</h2>
          <div className="space-y-2 text-[13px] leading-relaxed text-text-secondary">
            <p>이 화면은 읽기 전용입니다.</p>
            <p>실행 명령은 기존 CLI와 레거시 GUI 운영 흐름을 사용합니다.</p>
            <p>React 프론트는 현재 보기 좋은 room client 트랙입니다.</p>
          </div>
        </section>
      </div>
    </div>
  );
}
