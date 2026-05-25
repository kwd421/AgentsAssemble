import { useCallback } from "react";
import { fetchHealth, type HealthStatus } from "../api";
import { usePoll } from "../hooks";
import { X } from "lucide-react";

export default function AdminPanel({ onClose }: { onClose: () => void }) {
  const healthFetcher = useCallback(() => fetchHealth(), []);
  const [health] = usePoll<HealthStatus>(healthFetcher, 8000);

  const agents = health?.agents;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center justify-between px-4 py-2.5 border-b border-stone-200 bg-white">
        <span className="text-sm font-semibold text-stone-700">관리</span>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-stone-100 text-stone-400"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto chat-scroll px-4 py-4 space-y-5">
        <section>
          <h3 className="text-xs font-medium text-stone-500 mb-2">
            시스템 상태
          </h3>
          {health ? (
            <div className="space-y-1.5">
              <span
                className={`inline-block px-2 py-0.5 rounded text-xs ${
                  health.status === "ok"
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-amber-50 text-amber-700"
                }`}
              >
                {health.status === "ok" ? "정상" : "주의"}
              </span>
              {agents && (
                <p className="text-xs text-stone-500">
                  에이전트 {agents.live ?? 0}/{agents.total ?? 0} 활성
                  {agents.counts &&
                    Object.entries(agents.counts)
                      .filter(([, v]) => v > 0)
                      .map(([k, v]) => (
                        <span key={k} className="ml-2 text-stone-400">
                          {k} {v}
                        </span>
                      ))}
                </p>
              )}
              {agents?.attention && agents.attention.length > 0 && (
                <p className="text-[11px] text-amber-600 preserve-words">
                  주의: {agents.attention.slice(0, 3).join(", ")}
                  {agents.attention.length > 3 &&
                    ` 외 ${agents.attention.length - 3}건`}
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-stone-400">연결 확인 중…</p>
          )}
        </section>

        <section>
          <h3 className="text-xs font-medium text-stone-500 mb-1.5">참고</h3>
          <div className="text-xs text-stone-400 space-y-1 leading-relaxed">
            <p>이 화면은 읽기 전용입니다.</p>
            <p>실행 명령은 CLI를 사용하세요.</p>
            <p>레거시 GUI는 별도 포트에서 계속 사용 가능합니다.</p>
          </div>
        </section>
      </div>
    </div>
  );
}
