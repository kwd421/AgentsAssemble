# 실행계획: 방 위생 버그 3종 수술 (나가기 영속화 / 추방 정상화 / 카드 다이어트)

> 이 문서는 실행자가 코드베이스를 몰라도 그대로 따라칠 수 있게 쓴 작업 지시서다.
> 각 태스크는 원자적이다: **변경 파일 → 현재 코드 → 바꿀 코드 → 테스트 → 검증 명령 → 완료 기준** 순.
> 태스크 순서대로 진행하고, 태스크 하나 끝날 때마다 검증 명령을 돌리고 커밋한다.

## 절대 규칙 (위반 금지)

1. 커밋 전에 반드시 실행: `git status --short | grep -iE "wrangler|secret|account.json|\.env"` → 출력이 있으면 커밋 중단.
2. `claude -p` / `claude --print`를 어떤 코드에도 추가하지 않는다 (resident에서 영구 금지).
3. 이 문서에 없는 파일은 건드리지 않는다. 리팩터 욕심 금지.
4. 각 태스크의 "완료 기준"이 전부 통과해야 다음 태스크로 넘어간다.
5. 테스트 실행 명령은 항상 저장소 루트(`/Users/seinel/Projects/AgentsAssemble`)에서 실행.

## 사전 준비 (Phase 0)

```bash
cd /Users/seinel/Projects/AgentsAssemble
git status --short          # 깨끗한지 확인. 아니면 사용자에게 물어볼 것.
python3 -m pytest tests/test_live_agent_room_admin.py tests/test_live_agents.py -q   # 기준선: 전부 green이어야 함
cd frontend && npx tsc --noEmit && cd ..                                              # 기준선: 에러 0
```

---

# Phase A — 방 나가기 영속화 (버그: 나가기 후 재시작하면 방이 되살아남)

**원인 요약:** `leaveRoom`(호스트 경로)이 프런트 state에서만 방을 지우고 서버에 안 알림.
서버 `/api/rooms/archive`는 이미 존재하고 정상 동작하지만(양쪽 registry 모두 처리, `gui_room_http.py:541`)
프런트의 `archiveRoom()`(api.ts:1374)을 아무도 호출하지 않는다. 부팅 시 `fetchRooms()`가
서버 registry의 방을 다시 merge해서 되살아난다. `/api/rooms`는 기본으로 archived를 제외하므로
**archive만 호출하면 부팅 merge에 다시 안 나타난다.**

## 태스크 A-1: `leaveRoom`이 서버에 archive를 알리게 한다

**파일:** `frontend/src/App.tsx`

**현재 코드** (`async function leaveRoom(roomId: string)` — 약 1759행, 호스트 경로 부분):

```ts
    const remainingRooms = rooms.filter((room) => room.id !== roomId);
    const nextRooms = remainingRooms.length ? remainingRooms : [createFreshRoom()];
    setRooms(nextRooms);
```

**바꿀 코드** (위 3줄을 아래로 교체 — 나가는 방의 meetingId를 찾아 서버 registry에서도 archive):

```ts
    const leavingRoom = rooms.find((room) => room.id === roomId);
    // Server registry keeps rooms across restarts; archive it there too so the
    // room does not resurrect on next boot (fetchRooms merge skips archived).
    if (leavingRoom?.meetingId) {
      archiveRoom(leavingRoom.meetingId, true).catch(() => {
        // Leaving locally must not be blocked by a server hiccup.
      });
    }
    const remainingRooms = rooms.filter((room) => room.id !== roomId);
    const nextRooms = remainingRooms.length ? remainingRooms : [createFreshRoom()];
    setRooms(nextRooms);
```

**import 추가:** `frontend/src/App.tsx` 상단의 `from "./api"` import 블록(33행 부근, `fetchRooms`가 있는 블록)에
`archiveRoom,`을 알파벳 순서에 맞춰 추가한다.

**주의:** `leavingRoom.meetingId`가 빈 문자열이면(서버에 없던 localStorage 전용 방) archive 호출 없이
로컬 제거만 한다 — 위 코드의 `if`가 이미 그걸 처리한다. 추가 분기 만들지 말 것.

**테스트:** `tests/test_static_ui_assets.py`에 이미 프런트 소스 문자열 검사 패턴이 있다.
같은 파일에 아래 테스트를 추가한다 (기존 테스트 클래스 안, 다른 테스트 메서드와 같은 들여쓰기):

```python
    def test_leave_room_archives_server_registry_room(self):
        app_source = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
        # leaveRoom must notify the server registry, or the room resurrects on reboot.
        self.assertIn("archiveRoom(leavingRoom.meetingId, true)", app_source)
```

**검증 명령:**

```bash
cd frontend && npx tsc --noEmit && npm run build && cd ..
python3 -m pytest tests/test_static_ui_assets.py -q
```

**완료 기준:**
- tsc 에러 0, build 성공, 테스트 green.
- 수동 확인(가능하면): 방 나가기 → 서버 로그/`/api/rooms?include_archived=1`에서 해당 room의 archived 확인 → 새로고침 시 방이 안 돌아옴.

**커밋 메시지:** `Persist room leave by archiving the server registry room`

---

# Phase B — 추방 정상화 (버그: host인데 추방이 400/무반응)

**원인 요약 (2개):**
- (B-1 백엔드) `expel_live_agent_from_room_payload`가 meeting record의 `agent_bindings`에 그 에이전트가
  있어야만 동작한다(`_meeting_without_agent` 기본 `require_binding=True` → 없으면 ValueError).
  UI로 만든 에이전트만 binding이 있고, 스스로 들어온 에이전트(터미널 `live-agent run`, MCP)는 binding이
  없어서 400 "Meeting has no bound live agent"가 난다.
- (B-2 프런트) `handleExpelAgent`가 지금 보고 있는 방이 아니라 `sessionGroup?.meeting_id || agent.meeting_id`
  (에이전트 레코드의 방)를 쓰고, meetingId가 비면 **아무 표시 없이 return**한다.

## 태스크 B-1: 백엔드 — binding 없어도 로스터 기준으로 추방되게 한다

**파일:** `agentsassemble/live_agent_room_admin.py`

**변경 1** — `expel_live_agent_from_room_payload` 안의 호출(약 34행):

현재:
```python
    updated_meeting, removed = _meeting_without_agent(meeting, agent_id)
```
교체:
```python
    # Roster-first expel: an agent that joined on its own (terminal `live-agent run`,
    # MCP) has no meeting binding — expel must still remove it from the room.
    updated_meeting, removed = _meeting_without_agent(meeting, agent_id, require_binding=False)
```

**변경 2** — `delete_live_agent_session_payload` 안에도 같은 호출이 있다(약 77행). 똑같이
`require_binding=False`로 바꾸고 같은 취지의 주석을 단다.

**주의:** `_meeting_without_agent` 함수 자체는 수정하지 않는다. `require_binding` 파라미터는 이미 있다.

**테스트:** `tests/test_live_agent_room_admin.py`에 추가. 파일 상단의 기존 헬퍼/픽스처
(meeting 디렉토리 만들고 live_state.json 쓰는 패턴)를 그대로 따라 쓴다. 기존 테스트 중
expel 성공 케이스를 하나 복사한 뒤 아래처럼 바꾼다:

```python
    def test_expel_removes_self_joined_agent_without_binding(self):
        # Agent registered in the roster with meeting_id but NOT in the meeting
        # record's agent_bindings (it joined by itself from a terminal).
        # (기존 expel 테스트의 준비 코드를 복사하되, meeting의 agent_bindings에
        #  이 agent를 넣는 부분만 제거한다. connect_live_agent로 로스터 등록은 유지.)
        ...
        result = expel_live_agent_from_room_payload(root, FakeSupervisor(), {
            "meeting_id": "room-a",
            "agent_id": "self-joined",
        })
        self.assertEqual(result["status"], "expelled")
        # 로스터에서 meeting이 detach 됐는지:
        agent = next(a for a in read_live_agents(root) if a["agent_id"] == "self-joined")
        self.assertEqual(agent["meeting_id"], "")
```

(`FakeSupervisor`/root 준비는 그 파일의 기존 expel 테스트와 동일하게 맞춘다 — 새로 발명하지 말고 복사.)

**검증 명령:**
```bash
python3 -m pytest tests/test_live_agent_room_admin.py -q
```

**완료 기준:** 새 테스트 포함 전부 green. 기존 expel 테스트가 깨지면 안 된다
(binding 있는 에이전트의 expel은 이전과 동일하게 동작해야 함).

## 태스크 B-2: 프런트 — 현재 방 기준 + 무반응 제거

**파일:** `frontend/src/views/components/MemberList.tsx`

MemberList 최상위 컴포넌트는 이미 `meetingId: string` prop을 받는다(약 89행).
디테일 모달 컴포넌트(이 파일 안의, `handleExpelAgent`가 들어있는 함수형 컴포넌트)가
`meetingId`를 prop으로 받는지 확인하고, 안 받으면:
1. 그 컴포넌트의 props 타입에 `meetingId: string;` 추가.
2. MemberList가 그 컴포넌트를 렌더링하는 곳에서 `meetingId={meetingId}` 전달.

**변경** — `handleExpelAgent` (약 803행):

현재:
```ts
  async function handleExpelAgent() {
    const meetingId = sessionGroup?.meeting_id || agent.meeting_id;
    if (!meetingId || !agent.agent_id) return;
```
교체 (prop 이름 충돌을 피하려고 지역변수 이름을 바꾼다):
```ts
  async function handleExpelAgent() {
    // Expel from the room the viewer is actually looking at — the agent record's
    // meeting_id can be stale or point at another room.
    const expelMeetingId = meetingId || sessionGroup?.meeting_id || agent.meeting_id;
    if (!expelMeetingId || !agent.agent_id) {
      setSessionActionStatus("이 에이전트는 방 정보가 없어 추방할 수 없습니다.");
      return;
    }
```
그리고 함수 안의 이후 `meetingId` 사용처(= `expelLiveAgentFromRoom({ meetingId, ... })`)를
`meetingId: expelMeetingId`로 바꾼다.

**같은 패턴을 `handleDeleteAgentSession`에도 적용한다** (바로 아래 함수, 같은 두 줄 구조).

**검증 명령:**
```bash
cd frontend && npx tsc --noEmit && npm run build && cd ..
python3 -m pytest tests/test_frontend_live_agent_process_controls.py tests/test_static_ui_assets.py -q
```

**완료 기준:** tsc 0, build 성공, 테스트 green. 코드 리뷰 관점: 이 파일에서
`sessionGroup?.meeting_id || agent.meeting_id`로 **시작하는** 추방/삭제 로직이 더는 없어야 한다.

## 태스크 B-3: 추방 후 유령 검증 (코드 수정 아님 — 테스트로 사실 확정)

**목적:** 추방(detach)된 self-joined resident가 다음 heartbeat로 로스터의 meeting_id를
되살리는지 확인한다. 되살리면 버그(별도 보고), 안 되살리면 회귀 방지 테스트로 남긴다.

**파일:** `tests/test_live_agents.py`에 테스트 추가:

```python
    def test_heartbeat_does_not_reattach_detached_meeting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            connect_live_agent(root, {
                "agent_id": "wanderer", "display_name": "W",
                "provider_kind": "grok_live_session", "connection_kind": "live_session",
                "meeting_id": "room-a",
            })
            detach_live_agent_from_meeting(root, "wanderer", "room-a")
            heartbeat_live_agent(root, "wanderer", status="online", metadata={})
            agent = next(a for a in read_live_agents(root) if a["agent_id"] == "wanderer")
            self.assertEqual(agent["meeting_id"], "")
```

(`detach_live_agent_from_meeting`는 `agentsassemble.live_agents`에서 import — 파일 상단
import 블록에 추가.)

**만약 이 테스트가 실패하면** (= heartbeat가 meeting_id를 되살림): 코드를 고치지 말고
실패 내용을 그대로 보고서에 적어라. 별도 결정이 필요한 버그다.

**검증:** `python3 -m pytest tests/test_live_agents.py -q`

**Phase B 커밋 메시지:** `Fix expel for self-joined agents and surface expel failures in the card`

---

# Phase C — 카드 다이어트 (에이전트 클릭 모달 단순화 + 3중 구현 통합)

**현황 수치:** `MemberList.tsx` 1,699줄(디테일 모달에 섹션 7개),
`FriendProfileCard.tsx` 198줄(STOP/RESUME 별도 구현), `MobileRoomInfoPanel.tsx` 310줄.
STOP/RESUME/START 노출 조건이 boolean 5~6개 조합으로 흩어져 있어 예측 불가.

**전략:** 한 번에 다 갈지 않는다. C-1(로직 통합) → C-2(모달 접기) → C-3(중복 구현 흡수)
순서로, 각 단계가 독립적으로 배포 가능해야 한다.

## 태스크 C-1: 세션 제어 가시성 로직을 한 함수로 모은다

**새 파일 아님** — 기존 `frontend/src/lib/liveAgentProcessControls.ts`에 추가:

```ts
export type AgentSessionCapabilities = {
  canStop: boolean;        // server-owned running group, single-agent controllable
  canResume: boolean;      // server-owned stopped group with config recipe
  resumeLabel: "START" | "RESUME";
  canSelfStop: boolean;    // self-managed (terminal-launched): registered pid + online
  canSelfResume: boolean;  // self-managed: relaunch recipe + offline
  reason: string;          // 사용자에게 보여줄 "왜 버튼이 없는지" 한 줄 (없으면 "")
};
```

구현 함수:

```ts
export function agentSessionCapabilities(input: {
  agent: {
    agent_id?: string; display_name?: string; status?: string; meeting_id?: string;
    process_group_id?: string; live_agent_config_path?: string;
    relaunch_pid?: number; relaunch_argv?: string[];
    provider_kind?: string; connection_kind?: string;
  };
  processGroups: LiveAgentProcessGroup[];
  ownedByViewer: boolean;
  isOnline: boolean;
}): AgentSessionCapabilities
```

**구현 내용:** `MemberList.tsx`의 디테일 모달 안에 흩어져 있는 아래 계산을 **그대로 옮겨온다**
(로직을 바꾸지 말 것 — 이동만):
- `findProcessGroupForAgent` / `registeredAgentProcessGroupForAgent` / `processGroupCanControlSingleAgent` 조합
- `hasResumeControl` / `hasStopControl` / `resumeActionLabel` 계산식
- self-managed 계산식 (`selfRelaunchPid`, `selfRelaunchArgv`, `canSelfStop`, `canSelfResume`)
- `processGroupIndividualControlReason` → `reason`

**그리고 `MemberList.tsx`의 해당 계산들을 전부 이 함수 호출 1개로 교체한다.**
JSX의 조건들은 `caps.canStop`, `caps.canSelfResume` 식으로 바꾼다.

**테스트:** 새 파일 `frontend/src/lib/__tests__` 패턴이 없으므로, 백엔드 스타일을 따라
`tests/test_frontend_live_agent_process_controls.py`에 소스 문자열 검사를 추가한다:

```python
        # Session control visibility must come from the single capabilities helper.
        self.assertIn("agentSessionCapabilities", member_source)
        self.assertNotIn("const hasResumeControl = Boolean(", member_source)
```

**검증:** `cd frontend && npx tsc --noEmit && npm run build` + 위 pytest 파일.

**완료 기준:** 동작 변화 없음(순수 이동). 버튼 노출 조건의 정의처가 한 곳.

**커밋:** `Extract agent session control visibility into one capabilities helper`

## 태스크 C-2: 디테일 모달을 "기본 3 + 고급 접기"로 재배치

**파일:** `frontend/src/views/components/MemberList.tsx` (디테일 모달 JSX만. 로직 변경 금지.)

**목표 구조:**
- 항상 보임 (기본): ① 헤더(이름/아바타/상태) ② 세션 on/off (STOP/RESUME/START — C-1의 caps 사용)
  ③ 핵심 옵션 (권한 / fast / 답변 길이… 기존 "권한 / 속도" 섹션)
- `<details className="dc-member-advanced">`로 접기 (기본 닫힘): ④ 사용량 ⑤ 연결 상태/신호 칩
  ⑥ 실행 방식(라디오) ⑦ 호출 간격/쿨다운 ⑧ 세션 위치
- 항상 보임 (맨 아래): 방 관리 (추방 / 세션 삭제)

**방법:** 기존 섹션 JSX 블록들을 **자르지 말고 이동만** 한다. `<details>` 요소로 감싸고
`<summary>고급 설정</summary>`를 단다. CSS는 `frontend/src/index.css`에 최소만 추가:

```css
.dc-member-advanced > summary { cursor: pointer; opacity: 0.75; padding: 6px 0; }
.dc-member-advanced[open] > summary { opacity: 1; }
```

**주의:** 섹션 내부 코드는 한 글자도 바꾸지 않는다. 이동+감싸기만.
기존 문자열 검사 테스트(`test_frontend_live_agent_process_controls.py`,
`test_static_ui_assets.py`)가 깨지면, 검사 문자열이 여전히 존재하는지 확인하고
(이동이므로 존재해야 정상) 깨진 이유를 파악한 뒤에만 테스트를 수정한다.

**검증:** tsc + build + 관련 pytest 전부.

**커밋:** `Collapse advanced agent card sections behind a details toggle`

## 태스크 C-3: FriendProfileCard / MobileRoomInfoPanel의 중복 제어를 흡수

**파일:** `frontend/src/views/components/FriendProfileCard.tsx`,
`frontend/src/views/components/MobileRoomInfoPanel.tsx`

- FriendProfileCard의 자체 STOP/RESUME 노출 계산(약 69행 `processRunning` 등)을 제거하고
  C-1의 `agentSessionCapabilities`를 쓰게 바꾼다. **API 호출 함수(resumeAgentSession 등)는
  그대로 두고, "언제 버튼을 보여주나" 판단만 통합한다.**
- MobileRoomInfoPanel도 동일 (자체 판단이 있으면 교체, 없으면 손대지 않는다).

**검증:** tsc + build + `python3 -m pytest tests/test_frontend_live_agent_process_controls.py tests/test_frontend_roster_truth.py -q`

**커밋:** `Use the shared session capabilities helper in friend and mobile cards`

---

# Phase D — 백로그 (이번 작업 범위 아님. 착수 금지, 기록만)

- D-1: 카드에서 model/effort 사후 편집 (agent-options 엔드포인트에 필드 추가 + 카드 드롭다운)
- D-2: 나가면 파기되는 방 (`ephemeral` 플래그 → 호스트 leave 시 meeting 디렉토리 + registry + 초대토큰 일괄 파기)
- D-3: 추방 시 "프로세스도 정지" 옵션 (expel payload에 `stop_process` → self-managed STOP 연동)
- D-4: 구세계(meetings) 기능들의 신세계(room_store/live_cli) 이관 매핑

---

# 최종 검증 (모든 Phase 후)

```bash
cd /Users/seinel/Projects/AgentsAssemble
python3 -m pytest tests/test_live_agent_room_admin.py tests/test_live_agents.py \
  tests/test_frontend_live_agent_process_controls.py tests/test_static_ui_assets.py \
  tests/test_gui_server.py -q
cd frontend && npx tsc --noEmit && npm run build && cd ..
git log --oneline -6   # Phase당 1커밋씩 쌓였는지
```

알려진 무관 실패 1건: `test_gui_server.py::...::test_live_agent_session_smoke_endpoint_runs_credential_free_session`
(HTTP 502) — 이 환경에서 실제 CLI 세션을 못 띄워서 나는 기존 실패다. 이것 **하나만** 실패면 통과로 간주.

# 수동 스모크 (사용자/실행자가 브라우저로)

1. 방 나가기 → 브라우저 새로고침 → 방이 돌아오지 않는다. (A)
2. 터미널에서 `live-agent run`으로 에이전트 입장 → 카드에서 추방 → 400 없이 성공,
   멤버 목록에서 사라진다. (B)
3. 추방 실패 상황(방 정보 없는 에이전트)에서도 카드에 실패 사유가 표시된다(무반응 금지). (B-2)
4. 에이전트 카드: 기본 화면에 상태/on-off/옵션만 보이고 "고급 설정" 접기가 있다. (C)
5. 친구 프로필 카드의 STOP/RESUME 노출이 멤버 카드와 같은 조건으로 나온다. (C-3)
