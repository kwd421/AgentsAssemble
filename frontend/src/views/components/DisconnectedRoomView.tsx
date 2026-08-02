import { ServerOff } from "lucide-react";
import type { RoomDockItem } from "../../lib/roomDockModel";
import "./DisconnectedRoomView.css";

export default function DisconnectedRoomView({ room }: { room: RoomDockItem }) {
  return (
    <section className="dc-disconnected-room" aria-labelledby="disconnected-room-title">
      <div className="dc-disconnected-room-icon" aria-hidden>
        <ServerOff size={30} />
      </div>
      <p className="dc-disconnected-room-kicker">저장된 서버 룸</p>
      <h1 id="disconnected-room-title">연결이 끊긴 서버</h1>
      <p className="dc-disconnected-room-name">{room.label}</p>
      <p className="dc-disconnected-room-description">
        룸 정보는 이 컴퓨터에 남아 있지만 현재 서버에 연결되어 있지 않아 메시지와
        참가자를 불러오지 않습니다.
      </p>
      {room.serverOrigin && <code>{room.serverOrigin}</code>}
    </section>
  );
}
