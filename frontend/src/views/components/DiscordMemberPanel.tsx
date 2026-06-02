import { useEffect, useMemo, useState } from "react";
import { Bot, Braces, Laptop, ShieldCheck, UserRound } from "lucide-react";
import {
  fetchRoomMembers,
  saveRoomMemberRole,
  type LifecycleProjection,
  type LifecycleRoleHint,
  type LiveAgent,
  type RoomFriendType,
  type RoomMember,
  type RoomMemberRole,
  type RoomMembersResponse,
} from "../../api";

const ROLE_LABELS: Record<RoomMemberRole, string> = {
  human: "사람",
  director: "디렉터",
  implementer: "구현",
  reviewer: "리뷰어",
  observer: "관찰자",
};

const ROLE_ORDER: RoomMemberRole[] = ["director", "implementer", "reviewer", "human", "observer"];

const PARTICIPANT_TYPE_LABELS: Record<RoomFriendType, string> = {
  human: "사람",
  subscription_ai: "구독형 AI",
  api: "API",
  local: "Local",
  unknown: "기타",
};

const PARTICIPANT_TYPE_ICONS = {
  human: UserRound,
  subscription_ai: Bot,
  api: Braces,
  local: Laptop,
  unknown: ShieldCheck,
};

function memberKey(member: RoomMember) {
  return `${member.meeting_id || ""}:${member.participant_id}`;
}

function roleFromHint(hint: LifecycleRoleHint): RoomMemberRole {
  const text = `${hint.role_id} ${hint.display_name}`.toLowerCase();
  if (/(director|lead|owner|manager|pm|planner|디렉터|팀장)/.test(text)) return "director";
  if (/(review|reviewer|critic|qa|audit|리뷰|검토)/.test(text)) return "reviewer";
  if (/(impl|implement|coder|engineer|builder|dev|구현|개발)/.test(text)) return "implementer";
  return "observer";
}

function memberFromHint(hint: LifecycleRoleHint, meetingId: string): RoomMember {
  return {
    meeting_id: meetingId,
    participant_id: hint.role_id,
    display_name: hint.display_name || hint.role_id,
    role: roleFromHint(hint),
    participant_type: "unknown",
    status: hint.admission_status,
    source: "lifecycle_role",
  };
}

function groupMembers(members: RoomMember[]) {
  return ROLE_ORDER.map((role) => ({
    role,
    members: members.filter((member) => member.role === role),
  })).filter((group) => group.members.length > 0);
}

function statusClass(status?: string) {
  if (status === "working" || status === "online" || status === "bound_to_meeting") return "bg-online";
  if (status === "error") return "bg-danger";
  if (status === "idle" || status === "ready") return "bg-idle";
  return "bg-text-muted";
}

function memberSubtitle(member: RoomMember) {
  return [
    PARTICIPANT_TYPE_LABELS[member.participant_type] || "기타",
    member.provider_kind,
    member.connection_kind,
  ]
    .filter(Boolean)
    .join(" · ");
}

export default function DiscordMemberPanel({
  meetingId,
  agents,
  lifecycle,
}: {
  meetingId: string;
  agents: LiveAgent[];
  lifecycle: LifecycleProjection | null;
}) {
  const [payload, setPayload] = useState<RoomMembersResponse>({
    meeting_id: meetingId,
    members: [],
    roles: ROLE_ORDER.map((role) => ({ id: role, label: ROLE_LABELS[role] })),
  });
  const [busyMemberId, setBusyMemberId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchRoomMembers(meetingId)
      .then((nextPayload) => {
        if (!cancelled) {
          setPayload(nextPayload);
          setError("");
        }
      })
      .catch((errorValue) => {
        if (!cancelled) {
          setError(errorValue instanceof Error ? errorValue.message : "멤버 역할을 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId, agents.length]);

  const members = useMemo(() => {
    const byKey = new Map<string, RoomMember>();
    for (const member of payload.members) byKey.set(memberKey(member), member);
    for (const hint of lifecycle?.role_hints ?? []) {
      const member = memberFromHint(hint, meetingId);
      if (!byKey.has(memberKey(member))) byKey.set(memberKey(member), member);
    }
    return Array.from(byKey.values());
  }, [lifecycle?.role_hints, meetingId, payload.members]);

  const grouped = useMemo(() => groupMembers(members), [members]);

  async function updateRole(member: RoomMember, role: RoomMemberRole) {
    const key = memberKey(member);
    setBusyMemberId(key);
    setError("");
    try {
      const nextPayload = await saveRoomMemberRole({
        ...member,
        meeting_id: meetingId,
        role,
      });
      setPayload(nextPayload);
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "역할 저장 실패");
    } finally {
      setBusyMemberId("");
    }
  }

  return (
    <section className="rounded-xl border border-[#1f2024] bg-[#2b2d31] p-3" aria-label="멤버와 역할">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[13px] font-black uppercase tracking-wide text-text-muted">
          Members — {members.length}
        </h2>
        <span className="rounded bg-[#1e1f22] px-2 py-1 text-[10px] font-black text-text-muted">
          역할 저장
        </span>
      </div>

      {error && (
        <p className="mb-3 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] text-danger preserve-words">
          {error}
        </p>
      )}

      {grouped.length === 0 ? (
        <p className="rounded-lg bg-[#313338] p-3 text-[12px] text-text-muted preserve-words">
          아직 표시할 참가자가 없습니다.
        </p>
      ) : (
        <div className="space-y-4">
          {grouped.map((group) => (
            <section key={group.role} aria-label={ROLE_LABELS[group.role]}>
              <h3 className="mb-1.5 px-1 text-[11px] font-black uppercase tracking-wide text-text-muted">
                {ROLE_LABELS[group.role]} — {group.members.length}
              </h3>
              <div className="space-y-0.5">
                {group.members.map((member) => {
                  const Icon = PARTICIPANT_TYPE_ICONS[member.participant_type] || ShieldCheck;
                  const key = memberKey(member);
                  return (
                    <article
                      key={key}
                      className="group flex items-center gap-2 rounded px-1.5 py-1.5 hover:bg-white/[0.04]"
                    >
                      <span className="relative grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#313338] text-text-secondary">
                        <Icon size={16} />
                        <span
                          className={`absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-[#2b2d31] ${statusClass(member.status)}`}
                        />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13px] font-bold text-text-primary preserve-words">
                          {member.display_name}
                        </p>
                        <p className="truncate text-[11px] text-text-muted preserve-words">
                          {memberSubtitle(member)}
                        </p>
                      </div>
                      <select
                        value={member.role}
                        onChange={(event) => updateRole(member, event.target.value as RoomMemberRole)}
                        disabled={busyMemberId === key}
                        aria-label={`${member.display_name} 역할 변경`}
                        className="max-w-[104px] rounded bg-[#1e1f22] px-2 py-1 text-[11px] font-bold text-text-secondary opacity-0 outline-none transition group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
                      >
                        {ROLE_ORDER.map((role) => (
                          <option key={role} value={role}>
                            {ROLE_LABELS[role]}
                          </option>
                        ))}
                      </select>
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
