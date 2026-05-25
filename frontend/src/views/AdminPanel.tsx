import { useCallback } from "react";
import { Activity, Shield, X } from "lucide-react";
import { fetchHealth, type HealthStatus } from "../api";
import { usePoll } from "../hooks";

export default function AdminPanel({ onClose }: { onClose: () => void }) {
  const healthFetcher = useCallback(() => fetchHealth(), []);
  const [health] = usePoll<HealthStatus>(healthFetcher, 8000);

  const agents = health?.agents;
  const ok = health?.status === "ok";

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
                  주의: {agents.attention.slice(0, 3).join(", ")}
                  {agents.attention.length > 3 &&
                    ` 외 ${agents.attention.length - 3}건`}
                </p>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">연결 확인 중...</p>
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
