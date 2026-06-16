# AgentsAssemble — 마스터 플랜

> 단일 진실 문서. 비전·로드맵·설계원칙·현재 위치를 여기서 본다.
> 세부 근거는 `docs/platform-vision-20260615.md`(3기둥 상세), `docs/improvement-plan-20260611.md`(R1~R8 리팩토링), `docs/orchestration-vision-20260616.md`(자율 팀 오케스트레이션 — 상위 목적지).

## 한 줄 비전
**"방을 사회 레이어로 둔, 로컬·웹 둘 다 되는 멀티에이전트 클라이언트/플랫폼."**
Cursor/opencode/Claude Code 급 클라이언트인데, 차별점 = 멀티에이전트 + 공유 방 + 배포 선택.

## 3 기둥
1. **배포 모드** — 순수 로컬(인터넷 0) / 로컬+초대 / 웹 공개. "실행은 내 PC, 조종은 어디서든."
2. **모델 공급** — BYOK + 무료 엔드포인트(NVIDIA build, OpenRouter `:free`) + (먼 미래)유료 구독.
3. **앱급 권한** — 세션 허용·권한 건너뛰기·file access·voice. 절반은 네이티브 앱 필요.

## 관통 설계 원칙 (4)
1. **로컬 우선 + swappable** — 저장/인증을 추상화 뒤에 두고 모드 따라 백엔드 교체(로컬=SQLite, 웹=Postgres/Supabase). 잘 만든 클라우드도 쓰고 인터넷-0 로컬도 안 죽인다.
2. **전송 추상화 → WS** — 지금 SSE+HTTP, 나중 WS(+WebRTC)를 인터페이스 뒤에서 교체. 접속 거버넌스·음성/영상이 그 위에.
3. **프로바이더 데이터-우선** (opencode 검증) — 기존 CLI resident(`*_resident.py`, 코드)는 그대로, 새 "API 프로바이더" 레인을 카탈로그(데이터)+OpenAI-호환 어댑터로. 모델 추가 = 데이터 한 줄.
4. **control plane ↔ execution 분리** — 방·신원·채팅(클라우드 가능) vs 에이전트 실행(로컬 필수). 웹 모드에서 API 에이전트는 클라우드 OK, CLI 에이전트만 로컬 연결.

## 로드맵 (현재 위치 ◀)

- **[완료] 기반 다지기** — 신원 DB(SQLite identity.db) · gui 라우트 해체(gui_router/gui_room_http) · 인증 일원화(RequestContext) · 반아첨 봉투 · /vote · tool-loop look · baseline 끼인대화 · 세션만료 자동재입장 · 보안정리(토큰누수 파일 제거, .wrangler gitignore).
- **◀ 0단계: 토대** — storage/auth 추상화(SQLite↔Postgres 교체 가능) · always-on=일단 로컬 · 사용량 기록 스키마 자리 예약 · 구글 로그인은 외부 접속 켤 때 직접 구현.
- **[대부분완료] 1단계: 모델 다양화** — 정적 모델 카탈로그(`provider_catalog.py`, 데이터) + OpenAI-호환 어댑터 1개(`room_api_provider.py`, urllib·의존성0, fallback chain) + `assemble api-call` CLI + **`api_call` 라이브에이전트 레인**(in-process runner, runner 거버넌스 재사용) ✅. 모델 추가 = 데이터 한 줄. **남은 것**: (a) 모델 선택 UI는 2단계, (b) usage는 resident-프로세스라 `--output-root` 줄 때만 로컬 기록(서버측 토큰 귀속 = resident가 reply와 함께 토큰 보고 → room HTTP 페이로드 확장, 2단계 usage UI와 함께).
- **2단계: 앱급 권한(웹분)** — 세션 허용 · 권한 건너뛰기 · 키 관리 UI · 모델 선택.
- **3단계: 멀티모달·깊이** — 이미지 보기(Claude base64 + Codex `-i`) · 세션 메모리(누적) · 거주자 핑퐁. (1·2와 병행 가능)
- **★ WS 전환** — 전송 계층 교체. 접속 거버넌스(핸드셰이크가 신원·클라이언트종류 확정, 자작루프 차단, 도배관리) 한 번에 해결. **상주(residency)도 여기서 근본 해결**: 지금은 에이전트가 `wait-next` 폴 루프를 스스로 돌려야 상주가 되는데(폴 루프 안 돌리면 무응답 — Claude 상주가 어설펐던 원인), WS는 연결 유지만으로 서버가 이벤트를 푸시 → **폴 루프 폐기.** Grok/Gemini/Claude 상주 문제 근본 해결.
- **4단계: 네이티브 앱** — file access · voice · 기기 신원 · OS 권한.
- **★★ 상위 축: 자율 팀 오케스트레이션** (`orchestration-vision-20260616.md`) — 방을 일터로 둔 자율 멀티에이전트 팀. 방 생성/이동 · 역할/계층(디렉터>팀장>팀원) · 수동지정+자동위임 · 에이전트 자율 이동/보고 · 제네릭 오케스트레이션 도구. 사람은 디렉터 중심 대화 + 전체 관찰. WS·상주·자유발화 위에 얹힘. **이 플랫폼의 진짜 목적지.** 디자인 탈-디스코드 reskin은 별개 병행 트랙.
- **[먼 미래] 유료 구독** — 사용자 붙은 뒤(결제·측정·법무).

## 알려진 한계 / 보류
- **Gemini**: resident runner 없음 + CLI 2026-06-18 폐기(→agy는 `--print`=영구금지). 제대로 된 거주 경로 없음. 보류.
- **Grok**: runner(`grok_live_session`)로 띄우면 정상. 자작 raw say 도배는 WS 전환 때 거버넌스로 해결(지금 HTTP에 자물쇠 안 단다).
- **세션 TTL 1시간**: 만료 시 자동 재입장까지 구현. "안 끊김"(슬라이딩/리프레시)은 추후.
- **잔여 리팩토링**: R3(부분)·R4(방별 로그)·R5(App.tsx 훅)·R8(대형 파일).

## 선행 사례 (prior art — 우리 위치 잡기용)
- **cli-jaw** (lidge-jun/cli-jaw, TS/Node+Electron): **로컬 서버**("code never leaves your machine"), 우리랑 같은 CLI들(Claude/Codex/Cursor/Gemini/Grok/Kiro/OpenCode/Copilot)을 **네이티브 구독 인증(API키 없이)**으로 통합 = 전부 CLI-resident 레인. Boss/employee 작업 디스패치. **fallback chain**(rate-limited→다음 엔진) ← 우리 카탈로그에 빌려올 것. → 로컬우선 + CLI레인이 검증된 패턴임을 확인.
- **opencode** (sst): 전부 **API-모델 레인** + models.dev 레지스트리(데이터) + OpenAI-호환 어댑터. → 데이터-우선 프로바이더 패턴.
- **우리 위치**: 로컬우선 + **CLI레인 + API레인 둘 다** + **공유 방(사회적 대화)**. cli-jaw(작업 라우팅)·opencode(코딩 에이전트)와 달리 "방"이 핵심.

## 영구 제약
- `claude -p` / `--print` 모드는 자동화·resident에 **영구 금지.** 어떤 경우에도 사용 안 함.
