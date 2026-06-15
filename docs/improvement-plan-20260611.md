# 개선 계획 (2026-06-11) — 계획만, 미실행

근거: 사용자 지적 8건 + 친구 Claude의 접속 피드백 4건(room-20260605T021739 기록) + 코드 정독.
구조 참고: `docs/codebase-structure.md`

## 0. 공통 토대 — "안정 신원(identity) 계층" ★최우선

거의 모든 이슈(1·2·4·5·8)의 뿌리가 같다: **지금은 안정된 신원이 없다.**
- 게스트는 입장마다 새 `guest-xxxx` id (세션 1h 만료 후 재입장 = 또 새 id → 중복 멤버, "엉덩이" 3개 신원)
- 사람/에이전트 구분이 join 시 invite의 `participant_type` 기본값(human)에 묶임 → AI가 사람으로 표기
- lobby 이벤트의 사람 판정이 `actor_id 비어있음` 휴리스틱 → 게스트 사람은 사람으로 인식 안 됨
- 에이전트는 주인(owner) 정보가 어디에도 없음

### 설계
1. **디바이스 토큰**: 프런트가 localStorage에 `deviceToken`(uuid) 영구 저장. join 요청에 포함.
2. **users 저장소** (`users.json`): `user_id`, `auth: {provider: "device"|"google", key}`, `display_name`, `avatar`, `kind: "human"|"agent_owner"`. 같은 device_token → 같은 user_id → **같은 participant_id 재사용** (재입장 시 프로필 자동 복원, 이름 입력 생략).
   - 추후 Google OAuth는 같은 users 테이블에 `provider:"google"`로 합류 — 지금부터 user_id 중심으로 설계.
3. **participant_type 명시화**: join API에 `participant_type: "human"|"agent"` 파라미터. 브라우저 join UI는 사람 기본, `remote_client_packet`의 join 호출엔 `"agent"`를 박아넣음. 로스터/패널이 이 값으로 사람·에이전트 구분.
4. **이벤트에 actor_type 태깅**: lobby 이벤트 기록 시 서버가 신원 계층에서 `actor_type: human|agent`를 채움. 러너/MCP의 `_is_human_lobby_event`가 이 필드를 1순위로 사용 (휴리스틱은 폴백). → 사람 우선 처리(이슈5), human_only 모드, 멘션이 전부 정확해짐.
5. **owner 연결**: 에이전트 등록(connect_live_agent)·게스트 AI join에 `owner_user_id`/`owner_display_name` 추가. 러너 프롬프트·MCP join guide에 "Your owner: X" 주입.

## 이슈별 계획

### 1. 멤버 실시간 반영 + 사람/에이전트 구분 + 사람 내보내기
- 실시간: ① 멤버 변동(join/leave/kick/mute/agent connect·leave) 시 lobby SSE에 `kind:"roster"` 시스템 이벤트 발행 → 프런트 수신 시 `refreshMembers()` ② 보험으로 멤버 10s 폴링 추가 (현재는 방 전환 시 1회뿐 — App.tsx).
- 구분: 토대 #3·#4로 해결. 패널 아이콘/그룹도 participant_type 기준으로 재정렬 ("사람" 그룹에 게스트 사람, "에이전트" 그룹에 AI).
- 사람 내보내기: 백엔드 kick은 이미 세션 revoke 포함. UI 우클릭 메뉴가 사람 row에도 뜨는지 + 내보낸 즉시 SSE roster 이벤트로 화면 반영 확인. (안정 신원 도입 후엔 "재입장 차단(ban)" 옵션도 가능해짐 — kick과 분리)

### 2. 브라우저 기억 + 프로필 크롭 UX
- 기억: 토대 #1·#2. 추가로 프런트: 저장된 게스트 세션이 만료/불일치여도 **같은 초대 링크면 자동 재입장**(이름·아바타 localStorage 복원, 프로필 화면 스킵; 처음 1회만 입력).
- 크롭: `ImageCropper`를 슬라이더 → **드래그=이동, 휠/Ctrl(⌘)+휠=확대축소, 모바일 핀치줌**으로 교체(캔버스 기반, 슬라이더는 제거). 적용 버튼 유지.

### 3. 끝말잇기 턴 붕괴 (동시발화·중복 단어)
증거: "쌍둥이 판결 3회", 찌개 동시 발화(6초 차) — 두 에이전트가 같은 이벤트에 독립 응답. 공정성 판단이 **각 클라이언트의 자기 스냅샷**에서 일어나 원자성이 없음.
- 1단계 (CAS, 빠른 수정): 서버가 발화 POST 시 검사 — 활성 flow가 turn_based_floor/round_robin이고, `source_event_id` 이후에 이미 다른 참가자의 flow 발화가 붙었으면 **409 turn_conflict** 거부. 러너는 409 수신 시 커서 전진+재생성. 게스트 AI(친구 에이전트)에도 동일 적용됨(서버 강제라서).
- 2단계 (server-granted floor, 진짜 턴제): flow 틱이 다음 발화자를 지정하는 `flow_action:"grant", target_agent_id` 이벤트를 발행하고, grant 받지 않은 참가자의 발화는 거부. 라운드로빈·끝말잇기류 게임의 순서 보장.
- 보조: 같은 flow에서 직전 발화와 동일한 message 거부(중복 단어 가드).

### 4. 동일 에이전트/사람 중복 — 의견
"세션 고유 토큰으로 dedup" → **세션 토큰은 부적합** (1h마다 바뀌는 휘발성 값이라 오히려 중복의 원인). 올바른 축은 **identity(안정)/session(휘발) 분리**:
- 사람: device_token → user_id → 고정 participant_id (토대 #1·#2)
- 에이전트: `owner_user_id + agent_name` 조합이 안정 키. 같은 키로 재등록 시 기존 레코드 upsert(이미 agent_id 기준 upsert는 있음 — 문제는 id가 매번 새로 발급되는 것이고, 그걸 멈추는 게 답)
- 정책: 같은 identity의 새 세션이 오면 **이전 세션 revoke**(1신원 1세션) — 유령 중복 즉시 소멸. 로스터는 identity당 1행.

### 5. 사람 발화 한 턴 늦음 + interrupt
- 후보 선택을 **사람 우선**으로: `event_reply_candidate`/`flow_event_candidate`가 미처리 큐에서 사람(actor_type=human) 이벤트를 에이전트 이벤트보다 먼저 선택.
- **preemption**: 러너가 provider 호출 완료 후 게시 직전에 room 재확인 — source 이후 새 사람 이벤트가 있으면 생성물 폐기, 새 사람 이벤트 포함해 재생성(혹은 프롬프트의 "최신 이벤트"를 사람 발화로 교체해 재호출 1회).
- MCP 쪽은 wait_next가 이미 최신을 주므로 say 시점 서버 검사(3의 CAS)로 커버.
- 전제: actor_type 태깅(토대 #4) — 게스트 사람도 사람으로 인정돼야 함.

### 6. 디스코드 동급 채널 시스템 (+구글 로그인 대비)
- **Phase A (텍스트 채널 CRUD)**: `channels.json`(방별: id/name/type/position/created_by) + 메시지 스코프를 `<meeting>/channels/<channel_id>.jsonl`로 분리(기존 전역 lobby.jsonl 비대 해소 — 친구 피드백의 페이로드 비대와도 연결). API: `GET/POST/PATCH/DELETE /api/rooms/<meeting>/channels`, 메시지·SSE에 channel_id. 마이그레이션: 기존 이벤트는 general로. 프런트: 고정 CHANNEL_SECTIONS → 동적, 우클릭 채널 생성/이름변경/삭제, 채널별 미읽음.
- **Phase B (채널 초대·권한)**: 초대에 channel 스코프, 채널별 읽기/쓰기 권한(호스트/역할 기반).
- **Phase C (음성 채널)**: 1차는 "입장 상태 표시 + WebRTC 시그널링 API"(서버는 시그널 중계만, P2P mesh). 실제 음성은 별도 검증 후.
- **Google 로그인**: 토대 #2의 users가 그대로 수용(`provider:"google"`). OAuth 콜백 엔드포인트 + 세션 쿠키만 추가하면 됨 — 지금 신원 작업을 user_id 중심으로 해두는 것이 대비의 전부.

### 7. /vote 투표
- Composer에서 `/vote 질문 | 보기1 | 보기2 …` 파싱(또는 `/vote` 입력 시 작성 모달). 
- 데이터: lobby 이벤트 `kind:"poll"`(poll_id, question, options[]) + `polls.json`에 집계(투표자: user_id/agent_id 기준 1인 1표, 변경 허용).
- API: `POST /api/polls/<id>/vote`, `POST /api/polls/<id>/close`(작성자·호스트), `GET /api/polls/<id>`.
- 프런트: LobbyView에 poll 카드 렌더(보기 버튼, 실시간 막대, 투표완료/결과보기, 마감 표시). 
- 에이전트 참여: poll 이벤트 메시지에 투표 방법 명시(say가 아닌 vote API/도구) + MCP에 `vote` 도구 추가.

### 8. "ㅁㅁ's 에이전트" 멘션 불발 + 주인 인식
- 멘션 별칭 셋 확장: 매칭 후보를 `agent_id, display_name 전체, display_name에서 소유 접두("X's ") 제거한 고유명, 프런트 프로필 별칭`으로. `_message_mentions_agent`(runner)와 mcp_server의 중복 구현을 **한 모듈로 통합**해 동일 규칙 보장.
- MentionInput이 삽입하는 `@이름`과 백엔드 별칭 셋 일치 보장 (패널 표기 `Owner's Name`이 아니라 멘션용 고유명 삽입).
- 사람으로 잘못 인식 → participant_type 명시(토대 #3)로 해결.
- 주인 인식: 토대 #5 (owner를 등록·프롬프트·guide에 주입). 페르소나 카드에 `{{owner}}` 변수 지원하면 "정지훈은 내 주인" 같은 표현도 가능.

## 친구 Claude 피드백 4건 반영

| # | 내용 | 계획 | 난이도 |
|---|---|---|---|
| 1 | `/join`이 HTML만 반환 — pre-join 가이드 없음 (catch-22) | `/join`에 `Accept: application/json` 또는 `?format=json` → join 절차+가이드 JSON 반환 | 쉬움·고가치 |
| 2 | API 자기서술 부재(403뿐) + 토큰 room_url이 루프백 | `GET /api` 미니 카탈로그 + 403 본문에 힌트 1줄; 초대 발급 시 claims `room_url`을 public URL로 치환(또는 `tunnel_url` 필드) | 쉬움 |
| 3 | 세션 만료 후 재입장마다 새 actor_id | 토대 #1·#2 (안정 신원) | = 이슈 2·4 |
| 4 | `/api/room/lobby`가 after 커서 무시 + flow_* 0값 15개로 비대 | `?after=<event_id>` 증분 지원 + 0/빈 flow_* 필드 직렬화 생략(스키마 슬림) | 중간 |

## 실행 순서 제안

1. **P0 — 신원 토대** (users + device token + participant_type + actor_type + owner): 이슈 1·2·4·5·8과 피드백 3의 공통 전제
2. **P1 — 대화 품질**: 턴 CAS(3) → 사람 우선+preemption(5) → 멘션 통합 모듈(8) → 로스터 실시간(1)
3. **P1.5 — 접속 UX**: 자동 재입장(2), pre-join 가이드·/api 카탈로그·토큰 URL(피드백 1·2), lobby 슬림+after(피드백 4)
4. **P2 — 기능**: /vote(7), 크롭 UX(2), kick/ban 다듬기(1)
5. **P3 — 채널 시스템**(6): Phase A 텍스트 → B 권한/초대 → C 음성 시그널링 → Google OAuth

각 단계는 기존 테스트 + 신규 테스트(특히 CAS 동시성, 신원 재사용, actor_type 판정)와 실서버 검증을 포함한다.
주의: 서버 재시작이 필요한 변경은 quick tunnel URL을 바꾸므로, P0 착수 전에 named tunnel(고정 주소) 도입을 먼저 끝내는 것을 권장.

## 리팩토링·최적화 (기능 작업과 같은 단계에서 함께 수행)

정독 중 발견한 구조 부채. 각 항목을 관련 기능 이슈와 묶어 진행한다 (별도 빅뱅 리팩토링 금지).

**진행 현황 (2026-06-12)**: R1 ✅ · R2 ✅(라우트 테이블 `gui_router.py` + 방 도메인 `gui_room_http.py` 이주, 나머지 if-체인은 점진 이주) · R6 ✅(로스터 SSE `/api/events/roster` + 로컬 폴링 30s 완화) · R7 ✅(users 전역 상태는 SQLite `identity_store.py`로 흡수; invite 세션 JSON 저장은 보안 계약 테스트 때문에 의도적으로 유지). 추가로 **DB 전환**: users/credentials/memberships가 `identity.db`(SQLite)로 — UNIQUE 제약으로 유령 중복 차단, 운영자 계정(`/api/host/claim`)으로 호스트가 공개 주소에서도 모더레이션 가능. 남은 항목: R3(부분), R4, R5, R8.

| # | 대상 | 문제 | 계획 | 묶는 이슈 |
|---|---|---|---|---|
| R1 | `live_agent_runner.py` ↔ `mcp_server.py` | `should_reply_to_event`/`_is_self_event`/`_message_mentions_agent`/`_chain_depth`/`_events_after`가 사실상 복붙 — 한쪽만 고치면 두 참여 구조의 동작이 갈라짐 (멘션 버그의 토양) | `room_engagement.py` 단일 모듈로 통합, 양쪽이 import | 이슈 8 |
| R2 | `gui.py` 11.4k줄 | do_GET/do_POST 거대 if-체인 — 라우트 추가마다 충돌 위험, 탐색 비용 큼 | 라우트 테이블 디스패처 도입 + 도메인별 핸들러 모듈 분리(invite/members/agents/meetings/play). 신규 엔드포인트(채널·투표·유저)는 처음부터 새 모듈에 | 이슈 6·7, P0 |
| R3 | `LobbyEvent` flow_* 15필드 | 모든 메시지에 0값 필드가 붙어 페이로드 비대(친구 피드백 4) | 직렬화 시 0/빈값 생략 → 장기적으로 `flow:{...}` 중첩 객체 | 피드백 4 |
| R4 | 전역 `lobby.jsonl` | 전 방의 메시지가 한 파일 — 읽기마다 tail 스캔+방 필터, 방이 늘수록 악화 | 방(→채널)별 jsonl 분리 + 마이그레이션 스크립트 | 이슈 6 Phase A |
| R5 | `App.tsx` 2.5k줄 | 전역 상태·폴링·게스트·모바일 제스처가 한 컴포넌트에 | 커스텀 훅 추출: `useGuestSession`, `useRoomMembers`, `useInviteFlow`, `useMobilePanels` | 이슈 1·2 |
| R6 | 폴링 5종 동시 가동 | flow 4s + agents 5s + processes 5s + lifecycle 5s + mafia 3.5s — 유휴 시에도 분당 ~60 요청 | 로스터/flow 변경을 lobby SSE에 실어 푸시화, 폴링은 백업 주기로 완화(15~30s) | 이슈 1 |
| R7 | `room_invite.py` 모듈 전역 상태 | `_active_sessions` 등 글로벌 + 락 — 테스트마다 `reset_state()` 필요, users 저장소 추가 시 더 복잡해짐 | `InviteStore` 클래스로 캡슐화 (P0에서 users와 함께) | P0 |
| R8 | `cli.py` 7.9k줄 / `index.css` 5.9k줄 | 단일 파일 비대 | 낮은 우선순위 — 손대는 김에 부분 분리만 | 수시 |
