# Autonomous Room Event-Wake Experiments - 2026-07-25

## Purpose

This experiment tested whether persistent provider sessions can participate in
a room without a 250 ms provider polling loop and without a server-side count
that stops agent-to-agent conversation after a fixed number of replies.

The target behavior is not "send every message to every model." The target is:

1. Persist a room event.
2. Wake a relevant persistent session because new room content exists.
3. Let that session observe only content after its durable cursor.
4. Let the session speak or remain silent.
5. Keep media available through the same observation boundary.

Only steps 1 through 3 are partially available in the current runtime. The
provider adapters do not yet implement step 4, and native media observation in
step 5 is not implemented.

## Method

All user-visible experiment actions used the frontend at
`http://127.0.0.1:8877/`:

- room selection;
- conversation-mode selection;
- session configuration and resume;
- the initial human message;
- session stop after observation.

No backend command was used to create a turn or inject a provider reply.
SQLite was queried read-only after the experiment to count durable
`message_final` events.

The real persistent sessions were:

| Display label in the existing room | Runtime model used | Effort |
| --- | --- | --- |
| Codex Sol Low | `gpt-5.6-luna` | low |
| Grok 4.5 Low | `grok-4.5` | low |
| Claude Opus 4.8 Low | `claude-sonnet-4-6` | low |

The stale display labels were not treated as evidence of the runtime model.
The model values above were confirmed in the frontend session details after
configuration. The profile names were intentionally not renamed during this
experiment.

## Variant A: Existing Relay Count

Prompt entered once through the frontend:

> 낡은 도시의 모든 공공 시계가 매일 자정마다 서로 다른 내일을 가리킨다면, 사람들은 어느 시계를 믿어야 할까? 방에 있는 사람들끼리 자유롭게 이야기해봐.

Result:

- Claude replied.
- Grok replied and addressed Codex.
- No Codex turn followed.
- Durable agent finals before the next human message: 2.
- First-to-last agent-final interval: about 13 seconds.

The provider sessions were healthy enough to answer. The conversation stopped
because the ambient path still applied a server relay-depth count. This was not
natural silence.

## Variant B: No Ambient Relay Count

The ambient-specific `max_agent_relay_depth` input, constant, rejection reason,
and routing branch were removed. Legacy `continuous` mode remains separate and
still has its existing bounded behavior.

Prompt entered once through the frontend:

> 도시의 모든 엘리베이터가 자정에 존재하지 않는 층 하나를 잠깐 연다면, 그곳을 조사해야 할까 아니면 영원히 봉인해야 할까?

Observed result:

- All three real sessions participated.
- The discussion referenced prior speakers and converged on a coherent
  conclusion.
- The useful discussion lasted roughly two minutes.
- The runtime did not stop at a fixed agent-reply count.
- The sessions were stopped through their frontend controls.

Durable results from the human source event until the final stop:

| Metric | Result |
| --- | --- |
| Total agent finals | 41 |
| Codex finals | 14 |
| Claude finals | 14 |
| Grok finals | 13 |
| First agent final | 2026-07-25 10:34:00 UTC |
| Last agent final | 2026-07-25 10:38:01 UTC |
| Measured first-to-last interval | about 4 minutes 1 second |
| Closure-like finals | 31 |

The intended observation window was two minutes. The run continued longer
while the frontend stop controls were being located. This overrun exposed the
failure more clearly and is not reported as a successful two-minute stop.

After the useful discussion converged, the providers emitted variants of:

- "여기서 닫자"
- "확인"
- "종료 유지"
- "."

Each visible final became a new room event and woke another session. The next
session was required by its adapter to return another visible message, so the
room could not become quiet on its own.

## What The Experiment Proved

### Confirmed

- Ambient routing is event-driven from committed `message_final` events.
- There is no 250 ms provider polling loop in this path.
- Persistent sessions can follow prior room speakers.
- Removing the ambient server count permits a real multi-agent discussion to
  continue beyond two replies.
- The ambient path no longer contains a hidden replacement count.

### Not Confirmed

- A provider can decide not to speak.
- A closed discussion becomes quiet without a manual stop.
- A five-minute idle observation wake works.
- A provider sees images, PDFs, audio, or other native media.
- The model directly watches the browser UI.

The runtime contract already supports `ProviderTurnResult(outcome="decline")`
and canonical `turn.decline`. However, the real Codex/Claude terminal runtime,
Grok ACP runtime, and the other current adapters return
`outcome="message"` for every successful assignment. Empty output is treated as
an error, not as a valid silent observation.

## Comparison With The Older Fable Room

The older `room-20260605T021739` log contains active exchanges separated by
natural-looking quiet periods, including longer idle gaps before later
activity. Its visible behavior is closer to the target than either experiment:

- Variant A stopped artificially because of a count.
- Variant B continued artificially because every wake required a message.

The useful property to recover is therefore not a different count. It is a
first-class observation result that can be either `speak` or `decline`.

## Media Boundary

Current room storage and frontend rendering can preserve media metadata, but an
ambient turn assignment does not deliver provider-native image, PDF, or audio
inputs. Media-only events are deliberately excluded from ambient selection.

No media experiment was marked successful. Adding a textual filename or a
server path to the prompt would not count as the agent seeing the media and
would violate the intended information boundary.

## Recommended Next Experiment

Keep the count removed. Add a provider observation contract before another
long real-provider run:

```text
room event committed
  -> durable observation wake
  -> adapter reads events after last_seen_seq
  -> adapter returns speak(content) or decline(reason)
  -> only speak(content) creates message_final
  -> both outcomes advance the observation cursor
```

Requirements:

- `decline` is structured adapter output, not a visible sentinel string.
- A decline never creates a chat message.
- A decline does not immediately wake another participant.
- No server chain counter decides when the discussion ends.
- Unsupported provider transports report that limitation instead of silently
  substituting forced speech.
- Native media is referenced by a server-controlled media handle and mapped to
  provider-supported input; secret paths and credentials remain hidden.

The optional five-minute idle wake should be added only after a quiet room can
remain quiet. Otherwise it will restart a closed discussion and consume tokens
again.

## Current Verdict

The fixed chain count was the wrong stopping mechanism and has been removed
from ambient mode. The resulting experiment also proves that count removal
alone is insufficient. Production-ready autonomous participation requires a
real structured decline path and native media observation before it can be
described as "agents freely watching and joining a room."
