# RimWorld 플러그인 프로토타입 진행 기록

상태 기준: 2026-08-11, 브랜치 `codex/rimworld-plugin-prototype`

이 문서는 현재 구현과 실제 실행에서 확인된 사실을 구분해 기록한다. 테스트 통과는 실제 provider·서버·WebSocket 동작의 대체 증거로 취급하지 않는다.

## 목표와 범위

현재 목표는 저장소에 포함된 first-party 플러그인 하나가 방의 중앙 화면을 대체하고, 서버가 결정론적 생존 시뮬레이션을 소유하며, DeepSeek 공식 API V4 Flash·Grok 4.5·Antigravity가 각자 정착민을 도구로 조작하는 수직 슬라이스를 실증하는 것이다.

외부 플러그인 마켓, 임의 코드 설치, RimWorld 원본 자산, 세계 지도·카라반·DLC·고급 제작과 연구는 범위 밖이다.

## 구현된 것

### 플러그인 경계

- `agentsassemble.plugin/v1` 매니페스트와 저장소 내 first-party 플러그인 registry
- 방 설정의 revision 보호를 받는 `activity_plugin`
- 플러그인별 격리 프로세스, 제한된 저장소, 재시작 시 마지막 수락 상태 복원
- `plugin.command`, `plugin.snapshot`, `plugin.delta`, `plugin.error` WebSocket 봉투
- 새 구독은 불완전한 과거 delta를 재생하지 않고 최신 완전 snapshot부터 시작
- 권한 없는 활성화와 잘못된 revision은 실패 폐쇄
- 중앙 RimWorld 화면과 보조 채팅 전환, iframe/MessageChannel 기반 웹 경계

### 생존 시뮬레이션

- 48×32 맵과 정지·1×·3× 속도
- 세 정착민, 욕구·기분·특성·기술·열정·관계·부상 상태
- 자원, 저장구역, 침대·벽·문·식탁·모닥불·작업대 건설
- 작업 우선순위 1–4, 작업 종류 순서와 거리 기반 동률 해소
- 이동, 욕구 감소, 작업 진행, 습격, 부상, 회복, 정신이상
- 위협 뒤 회복 시간과 provider 실패 시 현재 작업까지만 마치고 대기하는 상태

### 에이전트 도구와 provider 연결

- `rimworld.observe`, `rimworld.inspect`, `rimworld.act`, `rimworld.speak`
- RoomPortal과 MCP/호환 API 도구에서 동일한 구조화 인자 사용
- 모델 호출은 매 tick이 아니라 작업 완료·욕구 임계치·사회·story 사건에서 발생
- provider가 한 턴에서 만든 행동과 발언을 하나의 plugin command batch로 발행
- 플러그인이 행동을 거부한 경우 provider 세션 전체 실패로 승격하지 않도록 수정
- 플러그인 응답을 기다리다 timeout이 난 경우에는 적용 여부가 불명확하므로 명시적 실패 유지

### 하네스 관련 병행 구현

- 공통 실행 하네스 레지스트리와 `builtin | codex | claude | opencode | pi`
- OpenCode 구조화 스트림과 Pi JSONL RPC 경로
- Freebuff PTY 모델 라벨 탐색 경로

이 하네스 항목은 코드에 들어갔지만, 계획에서 요구한 동일 DeepSeek 상류 모델의 네 하네스 실제 비교와 Freebuff DeepSeek 실제 완료는 아직 최종 실증되지 않았다.

## 실제 실행으로 확인한 것

실행 루트: `/tmp/agentsassemble-rimworld-final4.biKu8U`

이 경로는 임시 실행 증거이며 장기 보존을 보장하지 않는다. 아래 수치는 SQLite와 세 provider mirror를 교차 확인해 이 문서에 고정했다.

방: `rimworld-final-20260811T152853`

### 확인된 성공

- DeepSeek 공식 API V4 Flash가 실제 방에서 32턴 호출(29회 완료, 3회 발언 거절)
- 실제 plugin 도구로 관찰·건설·사회 발언 수행
- 최종 확인 상태: tick 7,734, speed 3, revision 2,612
- 자원: wood 2, steel 28, food 37
- 구조물: 침대 2, 모닥불 1, 저장구역 1, 벽 4
- 습격 시작·격퇴, 정신이상, 정착민 발언 이벤트가 실제 상태에 기록됨

### 실제 실행에서 발견한 실패와 수정

1. Grok은 자원 부족으로 plugin 행동이 거부되자 정상적인 게임 거부를 provider `turn.failed`로 승격해 세션이 종료됐다.
   - 수정: plugin NACK을 명시적 거부 결과로 처리하고 provider turn은 계속 사용할 수 있게 함.
   - 남은 확인: 수정한 코드로 실제 Grok이 거부 이후 다음 turn도 수행하는지 재실행해야 한다.

2. Antigravity는 room read와 `rim-observe` 시도 뒤 `agentsassemble-room help`를 실행했고, 이 읽기 전용 명령이 승인 요청으로 분류돼 180초 뒤 timeout됐다.
   - 수정: 인자 없는 정확한 `agentsassemble-room help`를 안전한 room 명령으로 허용.
   - 남은 확인: 실제 Antigravity가 승인 카드 없이 help→read→plugin 도구까지 진행하는지 재실행해야 한다.

3. HTTP `/api/agent-sessions` 생성은 세션 상태를 기록한 뒤 `Agent Bridge server URL is required`로 실패했다.
   - 원인: WebSocket 경로만 server URL과 bridge ticket issuer를 넘기고 HTTP 호환 경로는 동일 runtime handler를 사용하지 않았다.
   - 수정: HTTP와 WebSocket이 같은 runtime-aware command handler를 사용하게 함.
   - 남은 확인: 실제 HTTP 요청으로 프로세스가 시작되고 방에 연결되는지 확인해야 한다.

4. DeepSeek 사전 실행은 32턴을 호출해 계획한 provider별 최대 30회 상한을 2회 초과했다.
   - 이 실행은 동작 관찰 자료로는 유효하지만 호출 상한 검증에는 실패했다.
   - 남은 확인: 15분 실증 전에 30회 hard stop을 실제 provider 경로에서 재검증해야 한다.

## 아직 완료되지 않은 것

- 수정 후 Grok·Antigravity 실제 사전 실행
- DeepSeek·Grok·Antigravity 세 모델이 모두 성공하는 15분 고정-seed 실행
- provider별 최대 30회 호출 상한과 호출 수 기록
- 중앙 게임 화면, 보조 채팅, 속도 변경, 작은 화면, 재접속 복원을 실제 브라우저에서 확인
- WS gap 복구, plugin 프로세스 종료, 잘못된 revision과 권한 거부가 화면에 명시되는지 확인
- CPU·메모리·DB 쓰기 빈도 측정
- OpenCode·Pi·Codex·Claude 하네스의 동일 DeepSeek 실제 비교
- Freebuff에서 DeepSeek V4 Flash 실제 파일 읽기·수정·최종 응답
- 전체 프로바이더 실증 후 프론트 빌드와 실제 브라우저 검증

## 다음 실행 순서

1. 실제 HTTP 생성 경로로 Antigravity 한 세션을 시작한다.
2. 승인 요청 없이 help→read→plugin observe/act/speak가 이어지는지 서버 이벤트와 provider transcript에서 확인한다.
3. Grok에 거부될 plugin 행동을 실제로 발생시키고, 거부 뒤 다음 방 메시지에도 응답하는지 확인한다.
4. DeepSeek 실제 provider 경로에서 30회 hard stop과 호출 수 기록을 재검증한다.
5. 세 provider 사전 검증이 모두 통과한 뒤에만 15분 실증을 다시 실행한다.
6. 브라우저에서 게임 화면과 재접속을 확인하고 자원 사용량을 기록한다.
7. 하네스·Freebuff의 미완료 실제 비교를 수행한다. 지원하지 않는 기능이나 로그인·할당량 차단은 폴백하지 않고 그대로 기록한다.
8. 실제 실증을 마친 상태에서 프론트 빌드와 브라우저 경계를 확인하고, 남은 변경을 이유별로 커밋한다.

## 테스트에 대한 현재 판단

플러그인·네이티브 하네스 관련 신규 6파일의 76개 테스트 함수를 함수 단위로 다시 읽었다. 1차 판정은 `KEEP 52 / CONSOLIDATE 15 / REMOVE 9`였지만 그대로 삭제하지 않았다. 역검토에서 정신이상, provider 오류 후 대기, 3× 속도에서 provider 응답 대기 중 생존성, 작업 선택, 이동 후 작업 진행의 다섯 계약을 담은 네 테스트 함수가 잘못 낮게 평가됐음을 확인했다. 네 simulation 테스트의 삭제를 취소했고, unknown plugin ID 거부는 canonical 방 설정 검증으로 옮겼다. 중복 행동·revision 거부와 shallow manifest 확인은 더 강한 provider/process/permission oracle로 합치거나 제거했다. Native harness catalog 중복 1개와 Freebuff catalog 상수·runtime identity 2개도 중앙 registry 또는 실제 runtime 경계에 흡수해, 이 6파일은 최종 69개 함수가 남았다.

저장소 전체 감사와 실제 정리 내용은 `docs/reports/2026-08-11-test-suite-audit.md`에 분리해 기록한다. 플러그인 테스트 정리는 실제 프로바이더 실증을 대체하지 않는다. Grok·Antigravity 재실행과 3-provider 15분 실증, 실제 브라우저·자원 계측은 아직 미완료다.
