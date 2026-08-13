type RoomSyncIssue = {
  category: string;
  message: string;
};

function noticeMessage(category: string) {
  if (category === "room_directory_unavailable") {
    return "서버의 방 목록을 확인하지 못했습니다. 연결 상태를 확인한 뒤 새로고침해 주세요.";
  }
  if (category === "authorization_failed") {
    return "방 인증에 실패했습니다. 방에 다시 참여하거나 새 초대를 요청해 주세요.";
  }
  if (category === "socket_connection_failed") {
    return "방 서버에 연결하지 못했습니다. 연결을 다시 시도하고 있습니다.";
  }
  return "방 상태 불일치를 감지해 서버 원본으로 다시 동기화하고 있습니다.";
}

export default function RoomSyncNotice({
  issue,
}: {
  issue: RoomSyncIssue | null;
}) {
  if (!issue) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-room-sync-issue={issue.category}
      className="pointer-events-none fixed left-1/2 top-3 z-[220] w-[min(92vw,680px)] -translate-x-1/2 rounded-lg border border-amber-300/25 bg-[#29261f]/95 px-4 py-3 text-sm text-amber-50 shadow-xl backdrop-blur"
    >
      <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-amber-300" />
      {noticeMessage(issue.category)}
    </div>
  );
}
