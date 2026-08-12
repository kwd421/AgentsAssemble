# 테스트 스위트 함수 단위 감사

상태 기준: 2026-08-11, 비교 커밋 `31fb88e2`, 브랜치 `codex/rimworld-plugin-prototype`

이 문서는 테스트 개수나 통과율을 품질의 대리 지표로 쓰지 않는다. 각 테스트 함수가 어떤 계약을 보호하고, 실제로 어느 경계를 관찰하며, 제품이 깨진 상태에서도 통과할 수 있는지를 함수 본문과 직접 사용하는 fixture/helper까지 읽어 판정한 기록이다. 비교 커밋의 전체 4,275개 함수별 원장은 `2026-08-11-test-suite-audit.tsv`에 있다.

## 범위와 완전성

- `tests/test_*.py` 진입 파일 400개를 모두 확인했다.
  - 테스트 함수가 있는 파일 396개
  - 테스트를 직접 정의하지 않고 contract module을 모으는 aggregator 4개
- 이름이 `test_*.py`가 아니지만 aggregator가 가져오는 contract module 8개도 별도로 확인했다.
- fixture/helper/support 파일 10개는 직접 사용하는 테스트와 함께 읽었다.
- 비교 커밋의 테스트 함수 총수는 4,275개다.

첫 집계는 표준 `test_*.py`만 세어 4,226개였다. 파일 목록과 AST 함수 목록을 다시 대조하면서 비표준 contract module의 49개를 누락한 사실을 발견했고, 그 49개를 전부 추가 감사했다. 해당 49개는 SQLite/PostgreSQL durable repository, realtime controller, canonical event, 보안·lifetime 경계를 관찰하므로 모두 `KEEP`으로 판정했다. 이 보정 없이는 “전수”라고 부를 수 없었다.

## 판정 기준

- `KEEP`: 사용자 동작, durable state, HTTP/WS/provider/process 경계, 권한·보안 또는 실패 수명주기의 구체적 회귀를 잡는다.
- `CONSOLIDATE`: 계약은 의미 있지만 같은 계약을 더 강한 경계에서 반복하거나 여러 세부 case가 하나의 outcome matrix로 합쳐질 수 있다.
- `REMOVE`: symbol/export identity, source 문자열, exact copy/상수, mock 호출 전달, test-of-test, 또는 현행 제품 경계를 지나지 않는 obsolete wrapper만 확인한다.

fake나 patch를 썼다는 사실만으로 제거하지 않았다. 최종 oracle이 실제 HTTP 응답, provider 결과, process 종료, durable row/event, public projection 또는 권한 거부를 확인하면 유지했다. 반대로 이름이 `integration`, `contract`, `legacy`인지는 판정 근거로 쓰지 않았다.

## 1차 판정(역사적 기준선)

| 판정 | 함수 수 | 의미 |
| --- | ---: | --- |
| KEEP | 2,085 | 현재 계약을 직접 보호 |
| CONSOLIDATE | 963 | 의미는 있으나 중복·재배치 후보 |
| REMOVE | 1,227 | 현행 제품 회귀에 민감하지 않은 후보 |
| 합계 | 4,275 | 비교 커밋의 전체 테스트 함수 |

이 표는 최초 함수 단위 분류이며 자동 삭제 목록이 아니다. 특히 `REMOVE` 1,227개를 한꺼번에 지우지 않았다. 삭제 후보는 production consumer, 생성 문서, release/CI 명령, 대체 테스트를 다시 역추적한 뒤에만 적용했다.

## 남은 1,865개 후보 전수 역검토

HEAD `a383ea36`에서 보류 중이던 `CONSOLIDATE` 926개와 `REMOVE` 939개를 모두 다시 읽었다. 함수 본문과 직접 fixture/helper, production consumer와 공개 compatibility export, CI/release 호출 경로, 현재 남아 있는 대표 테스트를 대조했다. 상세 근거는 `2026-08-11-test-suite-reverse-review.tsv`의 1,865개 행에 있다. 원장의 `initial_*` 열은 1차 근거를 보존하고, `reverse_review_*`와 마지막 evidence 열은 이번 보정 판정을 기록한다.

이번 역검토는 다음처럼 더 엄격하게 판정했다.

- `KEEP_RESTORE`: 현재 제품·호환 경계의 독립 분기이며, 같은 회귀를 잡는 현재 대표가 없다.
- `SAFE_CONSOLIDATE`: 현재 남아 있는 exact representative가 같은 입력과 production 분기를 이미 실행하고, 그 assertion이 행에 적은 mutation에서 실패해야 함을 소스 수준으로 확인했다. 나중에 case를 추가하거나 parameterize할 수 있다는 가능성은 포함하지 않았다.
- `SAFE_REMOVE`: live consumer가 없거나, 이름·copy·상수·protocol identity·mock 전달만 확인하고 독립 제품 경계에는 기여하지 않는다. 중요한 실제 계약이 따로 미검증이면 그 공백도 행별 근거에 명시했다.
- `UNRESOLVED`: 계약은 중요하지만 현재 테스트의 oracle이 prompt/copy/private helper/mock call에 머물고, 이를 대신할 public/durable/process/browser 경계도 아직 없다. 테스트는 보존하지만 성공 증거로 세지 않는다.

| 역검토 판정 | 함수 수 | 현재 조치 |
| --- | ---: | --- |
| KEEP_RESTORE | 1,639 | 유지 |
| SAFE_CONSOLIDATE | 74 | exact 대표는 확인했지만 이번에는 삭제·흡수하지 않음 |
| SAFE_REMOVE | 49 | 46개 제거, 품질 게이트에 걸린 3개 유지 |
| UNRESOLVED | 103 | 유지하되 실제 계약은 미검증으로 기록 |
| 합계 | 1,865 | 보류 후보 전부 |

초기 판정이 역검토에서 어떻게 바뀌었는지는 다음과 같다.

| 1차 판정 | KEEP_RESTORE | SAFE_CONSOLIDATE | SAFE_REMOVE | UNRESOLVED | 합계 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CONSOLIDATE | 873 | 28 | 0 | 25 | 926 |
| REMOVE | 766 | 46 | 49 | 78 | 939 |

따라서 1차 후보 1,865개 중 1,639개는 독립 계약이 있거나 현재 대체 oracle이 없어 유지로 되돌렸다. 103개는 “필요 없음”도 “검증됨”도 아니다. 실제 경계가 없는 약한 테스트라서 보존과 검증 상태를 분리했다. 전수조사 직후 삭제·흡수를 검토할 수 있었던 것은 123개뿐이었다. 이후 사용자의 명시적 지시에 따라 그중 `SAFE_REMOVE` 49개만 적용 대상으로 삼았고, 46개를 제거했다. `SAFE_CONSOLIDATE` 74개와 `UNRESOLVED` 103개는 수정하지 않았다.

`SAFE_CONSOLIDATE` 74개 각각에 controlled production mutation을 실제 실행한 것은 아니다. 이번 판정은 현재 fixture·입력·production branch·assertion을 역추적한 정적 근거다. 따라서 이번 삭제에서 전부 제외했다. 이후 실제 흡수·삭제를 적용한다면 행별 mutation proof를 그 변경 단위에서 다시 남긴다.

## 현재까지 적용한 보수적 정리

- 비교 커밋의 기존 테스트 함수 371개를 제거하거나 더 강한 경계에 흡수했다.
  - 334개는 역검토까지 마친 `REMOVE`
  - 37개는 더 강한 경계로 합치거나 재배치한 `CONSOLIDATE`
- 통합 경계 테스트 5개를 새로 정의했으므로 함수 수의 순감은 366개다(`4,275 → 3,909`).
- 파일 전체가 symbol/export/package wrapper 또는 대체된 legacy registrar뿐인 테스트 파일 36개를 제거했다.
- 역검토의 `SAFE_REMOVE` 49개 중 46개를 추가 제거했다. `SAFE_CONSOLIDATE` 74개는 controlled mutation 증거가 없어 그대로 두었고, `UNRESOLVED` 103개도 그대로 두었다.
- 나머지 `SAFE_REMOVE` 3개는 삭제 자체보다 같은 파일의 기존 테스트가 품질 게이트에 걸려 유지했다. `test_live_agent_flow.py`의 parser/default 두 후보와 `test_room_repository_factory.py`의 default repository type 후보이며, 게이트를 우회하거나 관계없는 기존 테스트를 함께 고치지 않았다.

명시적으로 추가한 통합 oracle은 다음 다섯 개다.

- PostgreSQL strict runner가 prerequisite 누락과 DSN 노출을 실패 폐쇄하는 process outcome
- PostgreSQL strict runner가 skip·empty·failure를 성공으로 오인하지 않는 outcome matrix
- provider public artifact가 안전한 metadata와 env auth reference를 보존하는 계약
- provider public artifact가 endpoint query와 notes의 isolated secret을 제거하는 계약
- requester override 없이 실제 loopback HTTP 서버를 통과한 remote bridge가 공개 응답을 반환하는 계약

그 밖의 통합은 기존의 더 강한 테스트에 case를 합쳤다. 예를 들어 unknown activity plugin ID 거부는 plugin helper가 아니라 canonical room settings validation에서 확인한다.

## 역검토에서 취소하거나 보호한 삭제

초기 함수 판정을 그대로 적용하지 않았다. 다음은 역검토가 실제로 판정을 뒤집은 사례다.

- 정신이상, provider 오류 후 대기, 3× 속도에서 provider 응답 대기 중 생존성, 작업 선택, 이동 후 작업 진행의 다섯 RimWorld 계약을 담은 네 테스트 함수는 대체 테스트가 없었다. 삭제를 취소하고 `KEEP`으로 보정했다.
- unknown `activity_plugin` 거부는 direct helper 테스트를 없애되 canonical 방 설정 검증으로 옮겼다.
- plugin revision 누락과 한 턴의 두 번째 행동 거부는 각각 registry/process event와 provider tool adapter의 더 강한 테스트로 합쳤다.
- `tests/test_mcp_server.py`는 release health가 모듈을 직접 실행하며, MCP 참가자 권한·identity spoof·cursor·DM·archive 경계를 보호한다. 파일 삭제를 취소했다.
- provider input의 explicit instruction/JSON fallback은 package-export 테스트 파일 삭제 전에 실제 command/app-server turn 경계로 옮겼다.
- model credential marker와 public artifact redaction은 `test_models.py` 전체 삭제 전에 consumer-visible artifact 테스트로 옮겼다.
- PostgreSQL contract runner는 CI/Makefile의 실제 consumer가 있으므로 wrapper 파일처럼 통째로 삭제하지 않고 fail-closed outcome만 남겼다.
- `agentsassemble.room_users`는 동작 모듈이 아니라 호출자 0명의 최상위 re-export shim이었다. 이를 문자열로 참조하던 import-ban 테스트가 생성 지도에서 가짜 coverage를 만들고 있어 shim·문자열 테스트·호환 메타데이터를 함께 제거했다. 실제 current identity 동작은 `agentsassemble.application.room_users`의 HTTP/durable 테스트가 계속 보호한다.
- Claude bridge의 token 없는 startup 거부는 endpoint 인증과 별개인 fail-closed 계약이었다. `require_bridge_token`을 무력화한 controlled mutation에서 startup 테스트만 정확히 실패했으므로 초기 `CONSOLIDATE` 판정을 취소하고 `KEEP`으로 보정했다. 반면 production startup이 만들 수 없는 `_handler(token=None)` HTTP fixture는 실제 loopback 인증 대표 테스트와 중복이었다. GET 인증 조건을 무력화했을 때 대표 테스트가 401 누락으로 실패함을 확인한 뒤 이 fixture 하나만 흡수했다.
- Antigravity의 일반 `help/read` 허용과 RimWorld `rim-observe/inspect/act/speak` 허용은 같은 permission 결과를 내지만 production allowlist 분기가 다르다. `rim-*` 분기만 거부하도록 바꾼 controlled mutation에서 일반 read-only 테스트는 통과하고 RimWorld 테스트만 실패했다. 실제 provider/plugin 경로의 고유 회귀를 잡으므로 초기 `CONSOLIDATE` 판정을 취소하고 `KEEP`으로 보정했다.
- Claude print-mode compatibility bridge의 두 disabled-result 테스트 중 하나는 `returncode`와 `stderr` 기본값만 반복했다. provider runner를 호출하도록 controlled mutation했을 때 public disabled 결과와 runner side effect를 함께 보는 대표 테스트가 실패함을 확인했으므로 세부 metadata fixture 테스트 하나를 대표 경계에 흡수했다.

## 감사가 찾아낸 실제 제품 회귀

의미 있는 테스트는 정리 과정에서도 제품 버그를 드러냈다.

1. Agent Session 공개 final이 canonical visibility `visible` 대신 존재하지 않는 `public`을 기록하려 했다. provider process는 성공해도 durable event append에서 실패해 답변이 error로 바뀌었다. 공개 activity/final은 `VISIBLE`, 비공개 thinking은 `OWNER`를 사용하도록 교정했다.
2. loopback remote bridge 설정은 `http://127.0.0.1`을 허용했지만 adapter가 항상 HTTPS 전용 requester를 사용했다. 실제 smoke에서 local/live 두 답변만 기록되고 remote bridge는 timeout됐다. 검증된 loopback HTTP만 local requester로, HTTPS는 기존 remote requester로 분기했다. 전역 SSRF 정책은 느슨하게 만들지 않았다.

첫 회귀는 실제 turn→durable room event 테스트가 잡았고, 둘째는 supervised live smoke가 잡았다. loopback transport 선택에는 실제 HTTP 서버를 쓰는 adapter 회귀 테스트를 추가했으며, HTTPS-only requester로 되돌린 통제 mutation에서 동일한 `RemoteEndpointBlocked` 실패를 확인한 뒤 원복했다.

## 검증 원칙과 현재 한계

- 역검토 원장은 1,865개 고유 ID와 원 감사표의 대상 ID를 전부 대조했다. 누락·추가·중복은 0개였고, `SAFE_CONSOLIDATE` 74개가 가리키는 representative 참조 87개는 모두 현재 소스에 존재하며 최종 유지 상태임을 확인했다.
- `python3 -m pytest tests/test_claude_code_bridge.py tests/test_antigravity_provider_hooks.py tests/test_gui_room_repository_injection.py::GuiRoomRepositoryInjectionTests::test_handler_shares_one_explicit_repository_with_controller_and_routes -q`는 11개 테스트와 5개 subtest가 통과했다.
- 추가 제거 영역의 표적 테스트는 변경 모듈 묶음별로 재실행했다. frontend create를 제외한 세 묶음은 각각 `39 passed, 7 subtests passed`, `124 passed, 23 subtests passed`, `224 passed, 51 skipped, 47 subtests passed`로 통과했다.
- `tests/test_live_agent_frontend_create.py`는 현재와 HEAD 원본 모두 같은 12개가 `InviteRepositoryNotConfigured`로 실패했다. HEAD 원본은 11개 통과, 현재는 제거한 private helper 테스트 하나가 빠져 10개 통과했다. 이번 삭제가 만든 실패는 아니지만 해당 모듈은 여전히 통과 상태가 아니다.
- WebSocket composition의 `room_snapshot` callback은 동일 controller를 호출하고 snapshot을 반환하는 one-off smoke로 확인했다. 이 검사는 실제 WebSocket 연결·gap recovery 증거는 아니다.
- `python3 scripts/check_test_quality.py --base a383ea36`, `make architecture-check`, `git diff --check`가 통과했다.
- 사용자가 명시적으로 제외한 전체 4천여 개 재실행은 하지 않는다.
- PostgreSQL contract 중 일부는 실제 DSN/driver가 없으면 skip된다. SQLite 통과를 PostgreSQL 실측으로 과장하지 않는다.
- 이번 테스트 정리는 RimWorld의 Grok·Antigravity 재실행, 세 provider 15분 실증, 실제 브라우저·자원 계측을 대체하지 않는다.
- 기존 TSV의 1차 `verdict`는 역사적 판정으로 보존했다. 원 감사표의 `current_disposition`과 역검토 원장의 `application_disposition`은 역검토 후 유지, 미해결 유지, 미적용 exact 통합 후보, 실제 제거, 게이트 때문에 유지한 제거 후보를 구분한다.

## 후속 원칙

남은 후보를 정리할 때도 파일명이나 판정표만 보고 삭제하지 않는다. production consumer, CI/release command, durable/public 대체 oracle, 실제 실패 민감도를 다시 확인한다. 새 테스트는 계약·구체적 회귀·관찰 경계·실패시키는 production mutation 네 가지를 설명하지 못하면 만들지 않는다.
