import { useCallback } from "react";
import { Activity, ClipboardCheck, Shield, X } from "lucide-react";
import {
  fetchLocalResources,
  fetchReleaseHealth,
  fetchReleaseHealthQueue,
  type LocalResourceStatus,
  type ReleaseHealthCatalog,
  type ReleaseHealthQueue,
} from "../api";
import { usePoll } from "../hooks";
import { formatResourceMemory } from "../lib/localResourceLabels";
import {
  partitionReleaseHealthChecks,
  releaseHealthLatestById,
  releaseHealthStatusLabel,
} from "../lib/releaseHealthLabels";

export default function AdminPanel({ onClose }: { onClose: () => void; activeMeetingId?: string }) {
  const resourcesFetcher = useCallback(() => fetchLocalResources(), []);
  const healthFetcher = useCallback(() => fetchReleaseHealth(), []);
  const queueFetcher = useCallback(() => fetchReleaseHealthQueue(), []);
  const [resources, resourcesLoading, resourcesError] = usePoll<LocalResourceStatus>(
    resourcesFetcher,
    8000
  );
  const [catalog] = usePoll<ReleaseHealthCatalog>(healthFetcher, 30000);
  const [queue] = usePoll<ReleaseHealthQueue>(queueFetcher, 30000);
  const checks = partitionReleaseHealthChecks(catalog);
  const latest = releaseHealthLatestById(queue);

  return (
    <div className="ops-panel ops-cut mx-auto flex h-full min-h-0 max-w-5xl flex-col overflow-hidden">
      <header className="flex shrink-0 items-center justify-between border-b border-accent/14 px-5 py-4">
        <div className="flex items-center gap-3">
          <span className="hex-badge"><Shield size={17} /></span>
          <div>
            <h1 className="text-[20px] font-black">관리</h1>
            <p className="text-[12px] text-text-muted">현재 로컬 엔진의 읽기 전용 상태</p>
          </div>
        </div>
        <button type="button" onClick={onClose} className="ops-button grid h-10 w-10 place-items-center rounded-lg" aria-label="관리 닫기">
          <X size={16} />
        </button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5 chat-scroll">
        <section className="ops-inner rounded-xl p-5">
          <div className="mb-4 flex items-center gap-2">
            <Activity size={17} className={resources?.status === "ok" ? "text-online" : "text-idle"} />
            <h2 className="text-[15px] font-black">로컬 리소스</h2>
          </div>
          {resources ? (
            <div className="grid gap-3 text-[13px] text-text-secondary sm:grid-cols-2 lg:grid-cols-4">
              <Metric label="상태" value={resources.status === "ok" ? "정상" : resources.status} />
              <Metric label="CPU" value={String(resources.cpu_count)} />
              <Metric label="표시 프로세스" value={String(resources.summary.process_count)} />
              <Metric label="RSS 합계" value={formatResourceMemory(resources.summary.total_rss_kb)} />
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">
              {resourcesError ? "리소스 정보를 불러오지 못했습니다." : resourcesLoading ? "확인 중..." : "정보 없음"}
            </p>
          )}
        </section>

        <section className="ops-inner rounded-xl p-5">
          <div className="mb-4 flex items-center gap-2">
            <ClipboardCheck size={17} className="text-accent" />
            <h2 className="text-[15px] font-black">릴리스 헬스</h2>
          </div>
          {catalog ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {[...checks.defaultChecks, ...checks.optInChecks].map((check) => {
                const result = latest.get(check.id);
                return (
                  <article key={check.id} className="ops-inner rounded-lg px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <strong className="text-[13px] text-text-primary preserve-words">{check.label}</strong>
                      <span className="text-[10px] font-black text-text-muted">
                        {releaseHealthStatusLabel(result?.latest_status || "not_run")}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] text-text-muted preserve-words">{check.category} · {check.kind}</p>
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">카탈로그 확인 중...</p>
          )}
        </section>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ops-inner rounded-lg px-4 py-3">
      <span className="text-text-muted">{label}</span>{" "}
      <strong className="text-text-primary">{value}</strong>
    </div>
  );
}
