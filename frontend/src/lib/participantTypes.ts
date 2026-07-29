import { Bot, Cloud, Cpu, Server, User, Wifi } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ParticipantType } from "../api";

export type ParticipantTypeMeta = {
  label: string;
  detail: string;
  icon: LucideIcon;
  tone: string;
};

export const PARTICIPANT_TYPE_OPTIONS: Array<{ id: ParticipantType; label: string }> = [
  { id: "human", label: "사람" },
  { id: "subscription_ai", label: "구독형 AI" },
  { id: "api", label: "API" },
  { id: "local", label: "Local" },
  { id: "remote", label: "Remote" },
  { id: "unknown", label: "미분류" },
];

export function participantTypeMeta(type: string): ParticipantTypeMeta {
  if (type === "human") {
    return { label: "사람", detail: "브라우저/초대 사용자", icon: User, tone: "human" };
  }
  if (type === "subscription_ai") {
    return { label: "구독형 AI", detail: "Claude · Codex · Cursor · Antigravity", icon: Bot, tone: "subscription" };
  }
  if (type === "api") {
    return { label: "API", detail: "직접 연결 API 프로바이더", icon: Cloud, tone: "api" };
  }
  if (type === "local") {
    return { label: "Local", detail: "LM Studio · Llama · Ollama", icon: Cpu, tone: "local" };
  }
  if (type === "remote") {
    return { label: "Remote", detail: "외부 룸 클라이언트/브릿지", icon: Wifi, tone: "remote" };
  }
  return { label: "미분류", detail: "타입 확인 필요", icon: Server, tone: "unknown" };
}
