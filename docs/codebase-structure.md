# AgentsAssemble 코드베이스 구조 (2026-07-10 기준)

> 현재 native CLI Agent Session의 RoomStore 상태 소유권, 단일 WebSocket,
> Agent Bridge, PTY 수명주기는 `docs/live-cli-room-current-architecture.md`를
> 먼저 참고한다. 이 문서는 그 경로 밖의 전체 코드베이스 지도를 주로
> 설명한다.

작업 참고용 전체 지도. 백엔드와 프런트에는 레거시 회의 경로와 새 shared-room
MVP가 함께 남아 있다. **새 native CLI Agent Session 작업에서는 아래의 레거시
JSONL/폴링 설명을 현재 권위로 해석하지 말고**
`docs/live-cli-room-current-architecture.md`의 SQLite + 단일 WebSocket 경계를
따른다.

백엔드 ~56k줄(Python), 프런트엔드 ~22k줄(React/TS).
단일 서버 프로세스(`python -m agentsassemble.cli gui`, 기본 127.0.0.1:8765)가
HTTP API + SSE + 정적 프런트엔드 서빙을 전부 담당하는 **local-first** 구조다.
저장은 혼합형: 신원·로스터(users/credentials/memberships)는 **SQLite `identity.db`**(`identity_store.py`),
대화·이벤트는 `.agentsassemble/` 아래 **JSON/JSONL 파일**(lobby/live/side_chat), 초대 세션은 별도 JSON 스토어.
(웹 배포 모드에선 SQLite↔Postgres로 교체 가능하게 추상화 — `docs/platform-vision-20260615.md` 참고.)

---

## 1. 한 장 요약

```
브라우저(호스트/게스트) ─┐
원격 AI(HTTP 클라이언트) ─┼─ HTTP/SSE ─→ gui.py (단일 스레드풀 서버)
MCP tool-loop 에이전트  ─┤                ├─ lobby.jsonl        (방 채팅, 전 방 공유 파일 + flow_meeting_id로 스코프)
러너 관리 에이전트       ─┘                ├─ live_agents.json   (에이전트 레지스트리/프레즌스)
                                          ├─ room_members.json  (로스터 + 뮤트)
                                          ├─ room-invite-state.json (초대/세션 토큰, nonce)
                                          ├─ side_chat.jsonl    (스레드/사이드챗)
                                          └─ <meeting_id>/      (회의 디렉토리: live_events.jsonl, live_state.json, 아티팩트)
```

- "방(room)" = meeting_id. 채팅은 전역 `lobby.jsonl` 하나에 쌓이고 이벤트의
  `flow_meeting_id` 필드로 방을 구분한다 (읽기 시 필터링).
- 공개 접근은 cloudflared quick tunnel (`--start-public-tunnel`) — **재시작마다 URL 변경**.
- 호스트 권한 = `X-Host-Token` 헤더 (room_invite.py의 host token). 게스트 권한 = `Authorization: Bearer <session_token>`.

## 2. 백엔드 모듈 맵 (`agentsassemble/`)

### 2.1 서버·라우팅
| 파일 | 역할 |
|---|---|
| `gui.py` (8k+줄) | HTTP 서버 본체 + payload 빌더. `serve_gui()` 진입. 레거시 do_GET/do_POST if-체인은 라우트 테이블로 점진 이주 중. SSE: `/api/events/lobby`, `/api/events/side-chat`, `/api/events/roster`, `/api/room/events`, `/api/meetings/<id>/events` |
| `gui_router.py` | **R2 라우트 테이블 + RequestContext**. `@router.get/post` 등록, 디스패처. RequestContext가 신원 판정 단일 창구: `require_host`(호스트 토큰), `require_session`(게스트 세션), `require_moderator`(호스트 또는 운영자 세션), body 파싱 |
| `gui_room_http.py` | 방 도메인 라우트 모듈(초대/게스트 세션/로스터/모더레이션/`/api/host/claim`/roster SSE). 신규 엔드포인트는 if-체인이 아니라 이런 도메인 모듈에 등록 |
| `cli.py` (7.9k줄) | argparse 명령 트리: `gui`, `mcp serve`, `live-agent {register,run,run-group,room,say,lobby,heartbeat,leave,engagement,dm-reply,sessions,session-runs,processes,...}`, `invite`, `demo`, `persona`, `memory-capsule`, `health`, `claude-bridge` 등 |

### 2.2 방 데이터 계층
| 파일 | 역할 |
|---|---|
| `room_database.py` | **새 shared-room SQLite 권위**: rooms/participants/agent_sessions/room_events/command_results, seq 인덱스, 레거시 room JSON/JSONL 1회 검증 마이그레이션과 백업 |
| `room_store.py` | canonical room 도메인 저장 API. 브라우저 backfill, 참가자/Agent Session, command dedup이 모두 이 경로를 사용 |
| `room_context.py` | 새 세션의 RoomMemory + 최근 12개/4,000자 bootstrap, 이후 `last_provider_sync_seq` 뒤의 bounded diff 구성 |
| `room_types.py` | canonical room event/participant/session/command/turn packet 타입 계약 |
| `room_commands.py` | request-id command 검증과 서버 권위 capability 정책 |
| `room_routing.py` | `@mention`, `@all`, default responder, agent relay depth를 결정하는 순수 정책 |
| `room_event_broker.py` | bounded WebSocket fanout. delta를 먼저 버리고 essential overflow는 `resync_required`로 SQLite replay 유도 |
| `meeting_events.py` | `LobbyEvent`(40+ 필드: actor_id, side, auto_chain_depth, flow_* 15개, attachments), JSONL append/tail-read, `clean_lobby_text` |
| `identity_store.py` | **SQLite 신원 코어(identity.db)**: users(운영자 플래그)/credentials(device→user)/memberships(방별 로스터+뮤트+host, PK로 유령 중복 차단). 레거시 users.json/room_members.json 1회 마이그레이션 |
| `room_members.py` | 로스터 API(identity_store 위임) + 라이브 에이전트/세션 병합(`room_members_payload`): 초대 멤버 presence는 살아있는 세션 기준 재계산, 동명 stale 게스트 자동 접기, 역할(human/director/implementer/reviewer/agent), **muted**, kick |
| `live_agents.py` | 에이전트 레지스트리(live_agents.json). `connect_live_agent`(upsert by agent_id), heartbeat, stale 추론(180s), 커서 3종(lobby/live/dm) 저장 |
| `room_friends.py` / `room_friend_dms.py` | 친구 목록 + 1:1 DM |
| `user_profile.py` | 호스트 프로필 (이름/아바타/mic_muted) |
| `attachments.py` | 업로드 파일 (.agentsassemble/attachments) |

### 2.3 초대·보안·신원 계층
| 파일 | 역할 |
|---|---|
| `room_users.py` | 안정 신원 API(identity_store 위임). device_token(해시) → user_id/participant_id 고정 매핑, participant_type 정규화("agent"→"remote"), **운영자 계정**(`grant_operator_to_device`/`participant_is_operator`) — 운영자의 기기는 공개 주소로 들어와도 모더레이션 가능. 추후 Google OAuth가 같은 credentials 테이블에 합류 |
| `room_invite.py` | 초대 생성/입장/세션. max_uses(0=무제한 기본), permission_mode. join 시 device_token이 오면 **고정 participant_id 재사용 + 같은 신원의 이전 세션 자동 revoke(1신원 1세션)**, 프로필(이름) 기억. participant_type/owner_display_name 선언 지원. join 응답 `guide`에 owner 포함 |
| `multi_host_invites.py` | LAN 초대 토큰(HMAC-SHA256, `aai1.`), admission contract |
| `remote_room_client_packet.py` | AI용 입장 패킷(env/http/shell/instructions) |
| `invite_tunnel.py`(gui 내부 manager) | cloudflared quick tunnel 기동/상태 |

### 2.4 참가자 실행 계층 — ★ 3가지 참여 구조
| 구조 | 코드 | 동작 |
|---|---|---|
| ① **러너 관리(baseline/runtime)** | `live_agent_runner.py` `LiveAgentRunner.run()` → tick 루프 | 서버가 자식 프로세스로 러너를 띄우고, 러너가 room을 폴링→provider CLI exec/resume 호출→lobby POST. engagement_mode·cooldown·chain_depth·flow 공정성 전부 **러너(클라이언트) 측에서 판단** |
| ② **MCP tool-loop** | `mcp_server.py` (`assemble mcp serve --profile participant`) | provider(예: claude CLI)가 register/wait_next/say 도구로 직접 참여. register 시 커서 빨리감기(입장 전 백로그 미배달) |
| ③ **원격 클라이언트** | `room_invite.py` + `/api/room/say·lobby` | 외부(브라우저/친구의 AI)가 세션 토큰으로 REST 호출. **서버는 발화 타이밍을 전혀 제어하지 않음** |
| 프로세스 감독 | `live_agent_processes.py` `LiveAgentProcessSupervisor` | 러너 그룹 spawn/stop/restart/워치독, 그룹 manifest |
| 세션 수명 | `live_agent_sessions.py`, `live_agent_session_runs.py` | start/resume/check/readiness, 자동 reconcile |
| 생성 UI 백엔드 | `live_agent_frontend_create.py` | 프런트 "에이전트 추가" 모달의 옵션/생성/체크 |
| 디스커버리 | `live_agent_discovery.py` | 설치된 provider CLI 탐지 → config 생성 |
| preflight | `live_agent_preflight.py` | config 검증 (`-p`/`--print` 금지 가드는 `claude_resident.py`) |
| 전송 | `live_session_transport.py` | JSONL/PTY 터미널 세션 드라이버 |
| provider 어댑터 | `adapters/{registry,codex,remote_bridge,local_cli,http_llm}.py`, `*_resident.py`(codex/cursor/kiro/grok/antigravity/hermes/claude) | provider별 기본 커맨드/세션 |

새 shared-room MVP의 native CLI 경로는 위 레거시 3구조와 별도 메시지 버스를
만들지 않고, canonical `/ws?ticket=...`에 Agent Bridge principal로 참여한다.

| 파일 | 새 shared-room 책임 |
|---|---|
| `room_realtime.py` | command ACK/NACK, canonical append, turn orchestration, 1회 crash recovery 조정 |
| `native_cli_providers.py` | Codex Spark/Antigravity/Grok/Claude Haiku catalog, interactive command, runtime profile key (`claude -p` 금지) |
| `room_bridge_process.py` | 서버가 소유하는 Agent Bridge 프로세스 start/stop/restart |
| `room_agent_bridge.py` | 같은 room WebSocket에 인증해 turn assignment와 provider report를 중계 |
| `live_cli.py` | 장기 실행 PTY와 provider-owned transcript에서 자연어 assistant message 추출 |

핵심 메커니즘 (이슈 분석 시 필수):
- **공유 engagement 모듈** `room_engagement.py`: should_reply/멘션/자기·사람 판정/체인깊이를 runner와 mcp_server가 공동 import (중복 제거 완료).
- **커서**: 에이전트별 `last_observed_event_id`(lobby) / `last_observed_live_event_id`(공식) / `last_observed_dm_event_id`. 이후 이벤트만 후보.
- **chain depth**: 에이전트 답글은 `auto_chain_depth = 부모+1`. 기본 `max_chain_depth=1`.
- **engagement_mode**: always/mentioned/human_only/flow/moderator_called/watch/manual.
- **멘션 매칭**: 별칭 셋 = agent_id + display_name 전체 + **소유격 접두 제거 고유명**("X's 이름"→"이름", "X의 이름"). 한국어 조사 허용("페이블찡은" 매칭). `@이름`/`<@이름>`은 direct mention.
- **actor_type**: 모든 lobby 이벤트에 서버가 `human|agent` 스탬프 (`normalize_actor_type`; 레거시는 actor_id 휴리스틱 폴백). 사람 판정의 1순위 근거.
- **flow(Play Mode) 턴 직렬화**: `gui._flow_turn_conflict` — turn_based_floor/natural/round_robin에서 source 이후 다른 발화가 붙었으면 `turn_conflict`, 직전 발화와 동일 메시지는 `duplicate_flow_message` 거부(CAS). 러너는 conflict 응답을 에러 아닌 skip으로 처리(`_record_preempted`).
- **사람 우선 + interrupt**: 러너 후보 선택이 미답 큐에서 가장 오래된 **사람** 이벤트 우선(`_latest_human_reply_candidate`); provider 생성 후 게시 직전 room 재확인 — 새 사람 메시지가 있으면 생성물 폐기 후 다음 틱에 사람에게 응답(`_human_interrupt_arrived`, reply당 room GET 2회).
- **자기 이벤트 판정** `is_self_event`: actor_id 일치 또는 name==display_name.

### 2.5 회의(공식 레코드) 계층
`meeting.py`(생성) · `meeting_setup.py`(provider/permission 바인딩) · `meeting_phases.py` ·
`meeting_lifecycle.py` · `meeting_record.py` · `live_meeting_memory.py`(공유 메모리 rolling summary) ·
`live_agent_turns.py`(official turn request/reply) · `live_agent_finalization.py` · `memory_capsules.py`

### 2.6 기타
`persona_cards.py`(RisuAI 카드/charx/risum 임포트, 페르소나 프롬프트) · `mafia_game.py` ·
`provider_health.py` · `release_health.py` · `room_event_benchmark.py` · `live_agent_smoke.py`(스모크) ·
`local_resources.py` · `task_scope_report.py`

## 3. 데이터 파일 (`.agentsassemble/`)

새 shared-room MVP의 canonical 상태는
`.agentsassemble/rooms/rooms.sqlite3` 하나에 있으며, room별 디렉터리는 media,
handoff, bridge diagnostic, smoke artifact만 보관한다. 이전 `room.json`,
`participants.json`, `sessions.json`, `events.jsonl`은 검증 후 backup을 남기는
1회 migration input일 뿐 병렬 source of truth가 아니다. 아래 표는 주로
레거시 meeting/lobby 경로의 파일이다.

| 파일 | 내용 | 쓰는 곳 |
|---|---|---|
| `lobby.jsonl` | 전 방 공통 채팅 이벤트 (방 구분: flow_meeting_id) | append_lobby_event |
| `side_chat.jsonl` | 사이드챗/스레드 | append_side_chat_event |
| `live_agents.json` | 에이전트 레지스트리 (agent_id 키, 상태/커서/모드) | live_agents.py |
| `room_members.json` | 저장 로스터 (+muted) | room_members.py |
| `room-invite-state.json` | invite secret, 세션(지문 키), nonce, pending invites | room_invite.py |
| `room_settings.json`, `room_friends.json` | 방 외형/친구 | |
| `<meeting_id>/` | live_events.jsonl(공식·턴요청), live_state.json(참가자 바인딩), 아티팩트 md/json | meeting_events.py |
| `live-agent-created/<agent>.json` | 프런트 생성 에이전트 러너 config | live_agent_frontend_create |
| `attachments/`, `personas/` | 업로드, 페르소나 카드 | |
| `host-token` | 호스트 토큰 (서버 기동 인자로도 주입) | |

## 4. API 표면 (요약)

**공개(인증 불요)**: `GET /join?format=json`(pre-join 가이드, Accept: application/json 협상) · `GET /api`(카탈로그)

**공개(게스트 세션 토큰)**: `POST /api/room-invite/join`(device_token/participant_type/owner_display_name) ·
`GET /api/room/lobby?after=<id>`(증분) `?before=<id>&limit=`(히스토리 페이지) · `GET /api/room/events`(SSE) ·
`POST /api/room/say`(뮤트 차단) · `POST /api/room-invite/leave` · `POST /api/room-invite/companion` · `GET /api/live-agent-flow`

**히스토리 페이지네이션**: `read_lobby_before()` — JSONL 역방향 블록 스캔으로 방 필터 적용하며 페이지 채움 (`/api/lobby?before=` 도 동일). 초대 토큰 claims에 `public_room_url`(터널 주소) 포함.

**호스트 토큰 게이트**: `POST /api/room-invite/create|revoke` · `GET /api/room-invite/invites|sessions` ·
`POST /api/room-members/mute|kick` · `POST /api/public-invite/{host-token,public-url,tunnel/start,tunnel/stop}`

**로컬(게이트 없음 — 루프백 신뢰)**: 나머지 전부. 주요:
- 방: `GET/POST /api/lobby` · `GET/POST /api/side-chat` · `POST /api/lobby/promote` · `GET /api/room-members` · `POST /api/room-members`
- 에이전트: `GET/POST /api/live-agents` · `/api/live-agents/<id>/{room,lobby,heartbeat,leave,official-turn,dm-reply,engagement,probe,return-packet}`
- 세션/프로세스: `/api/live-agent-sessions/*` · `/api/live-agent-processes/*` · `/api/live-agent-session-runs/*` · `/api/live-agent-create*`
- flow: `POST /api/live-agent-flow/{start,stop}` · 회의: `/api/meetings*` · 턴: `/api/meetings/<id>/live-agent-turns/{request,call,sequence,rounds,round,preset}`
- 게임: `/api/play/mafia/*` · 친구: `/api/room-friends*` · 기타: `/api/user-profile`, `/api/attachments`, `/api/providers`, `/api/local-resources`, `/api/release-health*`

SSE 스트림: `/api/events/lobby`(전역) · `/api/events/side-chat` · `/api/room/events`(게스트용) · `/api/meetings/<id>/events`(공식 타임라인)

## 5. 프런트엔드 (`frontend/src/`)

- **`App.tsx` (2.5k줄)** — 단일 허브 컴포넌트. 모든 전역 상태(rooms dock, 활성 방, 채널, 게스트 세션, 멤버, flow, 모달들)와 폴링 보유.
  - 폴링: flow 4s · live-agents 5s · processes 5s · lifecycle 5s · mafia 3.5s. **room-members는 방 전환 시 1회 + 액션 후 refresh** (실시간 아님 — 이슈 1 원인)
  - SSE 구독: side-chat(상시), meeting events(live 채널 열람 시)
  - 게스트 모드: `guestLocked` 분기로 기능 축소. 게스트 세션은 localStorage(`roomGuestSession.v1`)에 저장, 같은 inviteToken일 때만 복원
- **`api.ts` (1.9k줄)** — 전 API 래퍼 + 타입 + SSE subscribe + 이벤트 병합 유틸. 호스트 토큰은 sessionStorage
- **views/**: `LobbyView`(채팅) · `LiveView`(공식 타임라인) · `BoardView` · `RecordsView` · `FriendsView` · `AdminPanel`
- **views/components/**: `MemberList`(로스터+상세 모달+우클릭 뮤트/내보내기) · `RoomConnectionPanel`(플레이모드 시작) · `LobbyComposer`(+`MentionInput`) · `RoomInviteModal` · `AgentCreateModal` · `RoomSettingsModal` · `UserPanel` · `SideChatDock` · `RoomRail`(서버독) · `ImageCropper`(슬라이더 방식) 등
- **lib/**: `roomDockModel`(방 dock 상태) · `roomGuestSession` · `agentLabels`(실행모드 라벨) · `agentQuotaVisibility` · `mentionComposerModel` · `presenceStatus` 등 — 뷰 로직과 분리된 순수 모델

채널 구조는 **고정 4채널**(general/stage-log/work-board/records) — 동적 채널 CRUD 없음 (이슈 6).

## 6. 메시지 전달 경로 (대표 시나리오)

```
[사람(호스트)] LobbyComposer → POST /api/lobby → lobby.jsonl → SSE /api/events/lobby → 모든 브라우저
                                                  ↓ (폴링)
[러너 에이전트] GET /api/live-agents/<id>/room (poll_interval 간격)
   → event_reply_candidate(커서/체인/engagement 필터) → provider CLI 호출(수 초)
   → POST /api/live-agents/<id>/lobby (source_event_id, depth+1) → lobby.jsonl
[MCP 에이전트] wait_next(서버측 롱폴) → say
[게스트/원격AI] 자체 폴링 GET /api/room/lobby → POST /api/room/say
```

⚠️ 후보 선택→provider 호출→POST 사이에 락이 없다. 같은 이벤트에 여러 에이전트가
동시에 응답 가능하고(끝말잇기 중복·쌍둥이 판결 현상), 생성 중 도착한 사람 메시지는
다음 tick까지 반영 안 됨(한 턴 늦은 응답).

## 7. 알려진 전역 제약

- 세션 토큰 TTL 1h (`SESSION_TOKEN_TTL_SECONDS`) — 만료 후 재입장 시 **새 guest id** 발급(재사용 초대)
- quick tunnel: 서버 재시작 = 공개 URL 변경. named tunnel 미도입
- `claude -p`/`--print` **영구 금지** (정책 + claude_resident.py 가드)
- 계정/로그인 없음 — 신원은 호스트(토큰)/게스트(세션)/에이전트(agent_id) 3종뿐
- 사람/에이전트 구분이 약함: lobby 이벤트는 `actor_id 유무`, 로스터는 `participant_type`(join 시 invite의 participant_type), 라이브 레지스트리는 provider_kind — 서로 일관성 보장 없음
```
