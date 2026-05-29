import { ClipboardCheck, FileText, MemoryStick, ShieldAlert } from "lucide-react";
import type { LifecycleProjection, WorkroomQueueEvidence } from "../../api";
import {
  summarizeWorkroomQueue,
  type WorkroomQueueLane,
  type WorkroomQueueTone,
} from "../../lib/workroomQueue";

function toneClass(tone: WorkroomQueueTone): string {
  if (tone === "danger") return "border-offline/45 bg-offline/10 text-offline";
  if (tone === "warn") return "border-idle/40 bg-idle/10 text-idle";
  if (tone === "online") return "border-online/35 bg-online/10 text-online";
  if (tone === "accent") return "border-accent/35 bg-accent/10 text-accent";
  return "border-line/60 bg-panel/45 text-text-muted";
}

function laneIcon(lane: WorkroomQueueLane) {
  if (lane.id === "blocked") return ShieldAlert;
  if (lane.id === "review") return ClipboardCheck;
  if (lane.id === "shared_memory") return MemoryStick;
  return FileText;
}

function laneCount(lane: WorkroomQueueLane): string {
  return typeof lane.total === "number" ? `${lane.count}/${lane.total}` : String(lane.count);
}

export default function WorkroomQueuePanel({
  lifecycle,
  evidence,
}: {
  lifecycle: LifecycleProjection | null;
  evidence: WorkroomQueueEvidence | null;
}) {
  const summary = summarizeWorkroomQueue({
    lifecycle: evidence?.lifecycle ?? lifecycle,
    artifacts: evidence?.artifacts ?? {},
    reviewCheckpointCount: evidence?.review_checkpoints?.count ?? 0,
    returnPacketCount: evidence?.return_packets?.count ?? 0,
  });

  return (
    <section className="ops-inner rounded-xl p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[17px] font-black text-text-primary">
            작업 큐 / 승인 게이트
          </h2>
          <p className="mt-1 text-[12px] text-text-muted preserve-words">
            lifecycle과 공식 산출물 상태만 읽어 지금 막힌 일과 검토 대상을 요약합니다.
          </p>
        </div>
        <span className="rounded-md border border-accent/20 bg-black/20 px-3 py-2 text-[11px] font-black text-text-muted">
          읽기 전용
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {summary.lanes.map((lane) => {
          const Icon = laneIcon(lane);
          return (
            <article key={lane.id} className="rounded-lg border border-accent/14 bg-black/16 p-3">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg border ${toneClass(lane.tone)}`}>
                    <Icon size={16} />
                  </span>
                  <div className="min-w-0">
                    <h3 className="text-[14px] font-black text-text-primary preserve-words">
                      {lane.label}
                    </h3>
                    <p className="mt-1 text-[11px] leading-relaxed text-text-muted preserve-words">
                      {lane.description}
                    </p>
                  </div>
                </div>
                <span className={`shrink-0 rounded border px-2 py-1 text-[11px] font-black ${toneClass(lane.tone)}`}>
                  {laneCount(lane)}
                </span>
              </div>

              <div className="space-y-2">
                {lane.items.length ? (
                  lane.items.map((item) => (
                    <div
                      key={item.id}
                      className="rounded-md border border-line/45 bg-panel/28 px-3 py-2"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <p className="min-w-0 text-[12px] font-black text-text-secondary preserve-words">
                          {item.label}
                        </p>
                        <span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-bold ${toneClass(item.tone)}`}>
                          {item.available === false ? "대기" : item.available ? "준비" : "확인"}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] leading-relaxed text-text-muted preserve-words">
                        {item.detail}
                      </p>
                    </div>
                  ))
                ) : (
                  <p className="rounded-md border border-line/45 bg-panel/28 px-3 py-2 text-[12px] text-text-muted preserve-words">
                    현재 표시할 항목이 없습니다.
                  </p>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
