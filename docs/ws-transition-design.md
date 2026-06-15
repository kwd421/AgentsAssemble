# WebSocket 전환 설계 (RoomTransport + WS 게이트웨이)

> 마스터 플랜 "★ WS 전환"의 설계 문서. 목적: 전송 계층을 추상화하고 WebSocket을
> 도입해 **상주(residency)·접속 거버넌스**를 근본 해결한다. (지금 SSE+폴 기반)
> 근거 맵: `docs/`(이 문서) + 전송 표면 매핑(2026-06-15, map-transport-surface 워크플로).

## 현재 상태 (맵 요약)
- 서버 = stdlib `http.server.BaseHTTPRequestHandler` (동기, async 아님, WS 미지원).
- 로비 = JSONL 파일. **pub/sub 없음.** SSE "푸시"는 사실 `_send_sse_stream()`이 200ms마다
  `_stream_snapshot_payload()`로 파일을 폴링→diff→프레임. (`sse_cadence.py`)
- 신원 = **요청당** Bearer 토큰 → 세션 dict (`verify_session_token`, `RequestContext`).
  연결당 상태 없음. 음소거/스코프는 매 POST 재조회. 레이트리밋 없음.
- 3개 표면: ① 이벤트 SSE(`/api/room/events`·`/api/events/roster|lobby|side-chat`·`/api/meetings/{id}/events`)
  ② 상주/툴루프 폴(`/api/live-agents/{id}/room` 250ms; `wait_next`) ③ 발화(`/api/room/say`).
- **기존 전송 추상화 없음.** 단 `IdentityBackend` Protocol이 따라 할 템플릿.

## 설계 원칙 (이 단계 한정)
1. **추가형, 빅뱅 금지.** `/ws`를 기존 HTTP/SSE **옆에** 추가. 기존 함수(`_stream_snapshot_payload`,
   거버넌스 append 경로) 재사용. 전체 HTTP 표면을 한 번에 Protocol 뒤로 감싸지 않는다(거대·위험).
2. **핸드셰이크가 신원·클라이언트종류를 한 번 확정** = 거버넌스 핵심. 연결당 상태 보유.
3. **전달은 v1에서 기존 snapshot 리더 재사용**(내부 폴링 유지). 진짜 push(append→broadcast)는
   `RoomEventHub` 후속 최적화로 분리(지연/효율 개선이지, 거버넌스 이득 아님).
4. **RFC 6455 순수 stdlib.** 의존성 0(현재 `mcp`만). 핸드셰이크(SHA1+base64) + 프레임 코덱 직접 구현·정밀 테스트.
5. **프런트 transport seam.** `WebSocketTransport` 우선 + 기존 SSE/폴 **폴백**(`SsePollTransport`). 플래그데이 없음.

## 인증: 단일사용 ws_ticket
브라우저 `WebSocket` 생성자는 `Authorization` 헤더를 못 단다. 긴수명 세션토큰을 URL/subprotocol에
노출하는 건 프라이버시 규칙 위반(쿼리스트링 금지). → **단일사용·짧은TTL `ws_ticket`**:
1. 클라가 인증된 `POST /api/ws-ticket`(Bearer 세션토큰) → 짧은 TTL 단일사용 ticket 발급.
2. `new WebSocket('/ws?ticket=...')`. 서버가 핸드셰이크에서 ticket 검증→소비→세션 바인딩.
ticket은 1회·수초 TTL이라 URL 노출 위험 최소. (resident/remote는 헤더 가능하니 Bearer 직접도 허용.)

## 연결 상태 (핸드셰이크에서 확정, 연결 수명 동안 보유)
```
WsConnection:
  agent_id, display_name
  participant_type   # human | agent
  client_type        # browser | resident | remote   (= 입구 통일: 에이전트는 이 경로로만 발화)
  invite_scope       # read_only | read_write
  meeting_id         # 연결당 단일 방 (멀티방은 연결 분리)
  operator: bool
  burst_bucket       # 연결당 토큰버킷 (도배/과속 = stateless HTTP에선 못 하던 것)
  cursor             # 클라가 보유, 재연결 시 resume_from_id
```
- **에이전트 자작 폴링루프 불가**: 소켓 위 유일한 쓰기 경로가 거버넌스된 `say` 프레임뿐.
- **음소거/스코프**: 핸드셰이크에서 로드, mute/unmute 브로드캐스트 시에만 갱신.
- **선제 종료**: 토큰 만료·kick을 서버가 즉시 프레임으로 통지(지금은 다음 API 호출 때까지 모름).

## 메시지 프로토콜 (WS 프레임 = JSON)
- C→S: `{op:"subscribe", streams:["lobby","roster","side_chat"], meeting_id, resume_from_id?}`
- C→S: `{op:"say", message, kind?, vote_*?}`  (서버가 name/actor_id/actor_type 주입 — 클라 위조 불가)
- S→C: `{op:"event", stream:"lobby", events:[...]}` / `{stream:"roster", members:[...]}`
- S→C: `{op:"ack", id}` / `{op:"error", category, message}` / `{op:"kicked"|"expired"}`
- ping/pong = RFC 6455 control frame (앱 keep-alive 불필요).

## 단계별 빌드
- **WS-3 ✅** `room_websocket.py`: RFC 6455 핸드셰이크 + 프레임 코덱(순수 stdlib) + 코덱 단위테스트(22).
- **WS-4 ✅** `ws_room_session.py`(WsTicketStore + WsRoomSession 프로토콜 코어, 16테스트) +
  gui.py `/ws` 소켓 하이재킹 + `POST /api/ws-ticket` + 핸드셰이크 신원·클라이언트종류 + 거버넌스 `say`
  + 이벤트 전달(snapshot 리더 재사용). 실소켓 통합테스트 3(ws-ticket→핸드셰이크→subscribe/say 왕복).
- **WS-5** 프런트 `RoomTransport` seam + `WebSocketTransport` + SSE/폴 폴백.
- **WS-6 (후속)** `RoomEventHub` pub/sub로 내부 폴링 은퇴(지연/효율).

## WS-4 통합 메모 (http.server 하이재킹)
서버는 `ThreadingHTTPServer` + `AgentsAssembleHandler(BaseHTTPRequestHandler)` (gui.py:8126).
**연결마다 스레드** → 장수명 WS 연결이 다른 요청을 막지 않음. `do_GET`(8165)에서:
1. `path=="/ws"` & `is_websocket_upgrade(self.headers)` 감지.
2. `?ticket=` → ws_ticket 검증 → 세션 해석(없으면 401/거부).
3. **101 응답을 raw로 작성**: `self.wfile.write(("\r\n".join(handshake_response_lines(self.headers))+"\r\n\r\n").encode())` + flush.
   (`send_response`는 Server/Date/Content-Length를 덧붙이므로 안 씀.)
4. 단일 스레드 루프: `select.select([self.connection],[],[], poll_interval)`로 짧게 대기 →
   (a) readable면 `self.rfile`에서 프레임 읽어 `MessageAssembler`로 처리(say/ping/close),
   (b) 매 틱 `_stream_snapshot_payload`로 새 이벤트 폴링→WS 프레임 push.
   = `_send_sse_stream`의 양방향판. pub/sub(WS-6) 전까진 내부 폴링 유지.
5. close/에러/`self.stop_event` 시 close 프레임 후 return → 연결 종료.
가짜 소켓(recv/sendall 큐) + 가짜 세션검증으로 단위테스트.

## 비목표 (이 단계에서 안 함)
- 전체 HTTP 표면의 Protocol 래핑(거대 리팩토링). 추가형으로 공존.
- 음성/영상(WebRTC) — WS 위에서 추후.
- pub/sub(WS-6로 분리). v1은 snapshot 리더 재사용.
```
```
