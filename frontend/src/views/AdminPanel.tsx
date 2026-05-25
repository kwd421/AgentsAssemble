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
    <div className="flex h-full flex-col overflow-hidden bg-chat-bg">
      <div className="flex shrink-0 items-center justify-between border-b border-black/20 px-4 py-3">
        <div className="flex items-center gap-2">
          <Shield size={16} className="text-text-muted" />
          <span className="text-[14px] font-bold text-text-primary">관리</span>
          <span className="rounded-full bg-panel-soft px-2 py-0.5 text-[11px] font-semibold text-text-muted">
            read only
          </span>
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1.5 text-text-muted hover:bg-panel-soft hover:text-text-primary"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4 chat-scroll">
        <section className="rounded-lg border border-panel-border bg-panel-soft/45 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Activity size={15} className={ok ? "text-online" : "text-idle"} />
            <h3 className="text-[12px] font-bold uppercase tracking-wider text-text-muted">
              시스템 상태
            </h3>
          </div>
          {health ? (
            <div className="space-y-3">
              <span
                className={`inline-flex rounded-full px-2.5 py-1 text-[12px] font-bold ${
                  ok ? "bg-online/15 text-online" : "bg-idle/15 text-idle"
                }`}
              >
                {ok ? "정상" : "주의"}
              </span>
              {agents && (
                <div className="grid gap-2 text-[12px] text-text-secondary sm:grid-cols-2">
                  <div className="rounded-md bg-chat-bg px-3 py-2">
                    활성 에이전트{" "}
                    <span className="font-bold text-text-primary">
                      {agents.live ?? 0}/{agents.total ?? 0}
                    </span>
                  </div>
                  {agents.counts &&
                    Object.entries(agents.counts)
                      .filter(([, value]) => value > 0)
                      .slice(0, 4)
                      .map(([key, value]) => (
                        <div key={key} className="rounded-md bg-chat-bg px-3 py-2">
                          {key}{" "}
                          <span className="font-bold text-text-primary">
                            {value}
                          </span>
                        </div>
                      ))}
                </div>
              )}
              {agents?.attention && agents.attention.length > 0 && (
                <p className="text-[12px] font-semibold text-idle preserve-words">
                  주의: {agents.attention.slice(0, 3).join(", ")}
                  {agents.attention.length > 3 &&
                    ` 외 ${agents.attention.length - 3}건`}
                </p>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-text-muted">연결 확인 중…</p>
          )}
        </section>

        <section className="rounded-lg border border-panel-border bg-panel-soft/45 p-4">
          <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wider text-text-muted">
            참고
          </h3>
          <div className="space-y-1.5 text-[13px] leading-relaxed text-text-secondary">
            <p>이 화면은 읽기 전용입니다.</p>
            <p>실행 명령은 CLI를 사용하세요.</p>
            <p>레거시 GUI는 별도 포트에서 계속 사용 가능합니다.</p>
          </div>
        </section>
      </div>
    </div>
  );
}
