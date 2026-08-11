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

## 최종 판정

| 판정 | 함수 수 | 의미 |
| --- | ---: | --- |
| KEEP | 2,083 | 현재 계약을 직접 보호 |
| CONSOLIDATE | 965 | 의미는 있으나 중복·재배치 후보 |
| REMOVE | 1,227 | 현행 제품 회귀에 민감하지 않은 후보 |
| 합계 | 4,275 | 비교 커밋의 전체 테스트 함수 |

이 표는 자동 삭제 목록이 아니다. 특히 `REMOVE` 1,227개를 한꺼번에 지우지 않았다. 삭제 후보는 production consumer, 생성 문서, release/CI 명령, 대체 테스트를 다시 역추적한 뒤에만 적용했다.

## 이번에 적용한 보수적 첫 정리

- 비교 커밋의 기존 테스트 함수 323개를 제거하거나 더 강한 경계에 흡수했다.
  - 288개는 역검토까지 마친 `REMOVE`
  - 35개는 더 강한 경계로 합치거나 재배치한 `CONSOLIDATE`
- 통합 경계 테스트 5개를 새로 정의했으므로 함수 수의 순감은 318개다(`4,275 → 3,957`).
- 파일 전체가 symbol/export/package wrapper 또는 대체된 legacy registrar뿐인 테스트 파일 30개를 제거했다.
- 현재 남은 930개 `CONSOLIDATE`와 939개 `REMOVE` 후보는 이번에 자동 삭제하지 않았다. 다음 정리는 다시 consumer와 대체 oracle을 확인해야 한다.

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

## 감사가 찾아낸 실제 제품 회귀

의미 있는 테스트는 정리 과정에서도 제품 버그를 드러냈다.

1. Agent Session 공개 final이 canonical visibility `visible` 대신 존재하지 않는 `public`을 기록하려 했다. provider process는 성공해도 durable event append에서 실패해 답변이 error로 바뀌었다. 공개 activity/final은 `VISIBLE`, 비공개 thinking은 `OWNER`를 사용하도록 교정했다.
2. loopback remote bridge 설정은 `http://127.0.0.1`을 허용했지만 adapter가 항상 HTTPS 전용 requester를 사용했다. 실제 smoke에서 local/live 두 답변만 기록되고 remote bridge는 timeout됐다. 검증된 loopback HTTP만 local requester로, HTTPS는 기존 remote requester로 분기했다. 전역 SSRF 정책은 느슨하게 만들지 않았다.

첫 회귀는 실제 turn→durable room event 테스트가 잡았고, 둘째는 supervised live smoke가 잡았다. loopback transport 선택에는 실제 HTTP 서버를 쓰는 adapter 회귀 테스트를 추가했으며, HTTPS-only requester로 되돌린 통제 mutation에서 동일한 `RemoteEndpointBlocked` 실패를 확인한 뒤 원복했다.

## 검증 원칙과 현재 한계

- 수정한 영역은 표적 테스트, test-quality gate, generated map check, architecture gate로 검증한다.
- 사용자가 명시적으로 제외한 전체 4천여 개 재실행은 하지 않는다.
- PostgreSQL contract 중 일부는 실제 DSN/driver가 없으면 skip된다. SQLite 통과를 PostgreSQL 실측으로 과장하지 않는다.
- 이번 테스트 정리는 RimWorld의 Grok·Antigravity 재실행, 세 provider 15분 실증, 실제 브라우저·자원 계측을 대체하지 않는다.
- TSV의 `current_disposition`은 실제 적용 여부를 구분한다. `retained_pending_*`는 감사 후보일 뿐 삭제 승인이나 완료를 뜻하지 않는다.

## 후속 원칙

남은 후보를 정리할 때도 파일명이나 판정표만 보고 삭제하지 않는다. production consumer, CI/release command, durable/public 대체 oracle, 실제 실패 민감도를 다시 확인한다. 새 테스트는 계약·구체적 회귀·관찰 경계·실패시키는 production mutation 네 가지를 설명하지 못하면 만들지 않는다.
