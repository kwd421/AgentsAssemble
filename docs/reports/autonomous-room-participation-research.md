# Autonomous Room Participation Research

Date: 2026-07-11

## Question

How can a persistent provider session participate in a shared room more like a
person: remain present without continuously spending tokens, notice text and
media, decide whether to speak, wait or follow up later, and avoid polling the
room every fraction of a second?

This report is design research. It does not authorize a provider migration or a
second room transport.

## Current Behavior

The canonical room transport is already event driven. A finalized message is
appended to `RoomStore`, broadcast over the room WebSocket, and passed to
`CanonicalRoomController._route_message_event()`.

The autonomy problem begins after that point:

1. `route_message_targets()` selects one or more provider sessions.
2. `_assign_pending()` creates a provider turn.
3. The provider input explicitly says that it is the agent's turn and requests
   a room-visible reply.
4. A selected provider therefore has no clean protocol-level way to stay silent.

The current `continuous` mode is bounded automatic alternation, not autonomous
conversation. The live `room-20260711T131220` settings use
`max_relay_turns: 20`; its event log shows the two agents alternating until that
limit after a human message.

Media has a second gap. The room can store and render attachments, and the turn
builder can construct a media manifest. However, the canonical external bridge
assignment currently sends the text `provider_input` without a media capability
contract or media payload/reference. The manifest also selects media only when
an id or filename appears in projected room text unless explicit media ids are
supplied. Current native bridge participation must therefore not claim that an
agent actually saw an attached image.

## What "Watching the Room" Can Mean

There are three different layers which should not be conflated.

### 1. Connected client

An agent-owned room client keeps the same authenticated WebSocket event stream,
cursor, roster, replies, reactions and attachment metadata as a browser. This is
cheap: the bridge process receives events, while the model is not running.

This is the recommended meaning of "present in the room".

### 2. Semantic attention

The client maintains an unread inbox and wakes the model only when an attention
policy says the new material may deserve a response. The model receives a
bounded diff plus explicitly selected media. Silence costs no provider call.

### 3. Pixel-level watching

A browser/computer-use agent repeatedly receives screenshots or accessibility
snapshots. This is useful for testing the actual human UI, but it is a poor
default room transport. Screenshot loops require repeated model turns, duplicate
information already present in room events, consume substantially more tokens,
and make attachment and identity semantics less reliable.

Playwright's agent interface uses accessibility-tree snapshots rather than raw
HTML for a more compact semantic view. Computer-use systems still operate as a
screenshot/action loop. A hybrid can expose a rendered room snapshot on demand,
but should not make screenshots the agent's primary inbox.

## Candidate Designs

| Design | Idle provider cost | Autonomy | Main weakness |
| --- | ---: | --- | --- |
| Fixed round robin | Zero between turns | Low | Every assigned agent must answer |
| Deterministic attention gate | Zero | Medium | Rules can miss subtle invitations |
| Central LLM speaker selector | One small model call per decision | Medium | Selector cost and bias |
| Per-agent self-selection call | One call per agent per event | High in appearance | Highest token use; agents over-speak |
| Blocking room-inbox tool | Zero while blocked | High | Provider/tool timeout compatibility |
| Local shadow listener | Local compute only while listening | High | Requires a suitable local classifier/VLM |
| Full browser/computer-use watcher | Continuous multimodal calls | Superficially high | Expensive, slow and brittle |

### A. Deterministic attention gate

The server builds candidates without calling a model. Strong signals include:

- direct mention or reply-to relationship;
- a question addressed to the room;
- the current speaker explicitly selecting the next speaker;
- an agent's outstanding promise or scheduled follow-up;
- topic/role match;
- cooldown, recent speaking share and consecutive-turn limits;
- explicit room mode and operator budget.

The gate may select zero speakers. This is the key behavior missing today.

### B. Central selector after deterministic filtering

AutoGen's `SelectorGroupChat` uses a model to select one next speaker and allows
a custom candidate function. The useful part for AgentsAssemble is the two-stage
shape: cheap code narrows the candidate set, then an optional small/local model
resolves only ambiguous cases. Running a selector over every event and the full
history would add cost and context growth, so it should be an opt-in second
stage, not the default gate.

### C. Blocking inbox tool

A structured provider session can call a `wait_for_room_attention` tool. The
bridge waits on an event/condition and returns only when the server grants an
attention lease. This is event waiting, not 250 ms polling, and consumes no
model tokens while the bridge is blocked.

This is the closest match to an agent independently waiting in a room, but it
must be tested provider by provider. Some CLIs may time out a long tool call,
finish the turn after the tool result, or cap tool-loop duration. PTY-only
providers may not support it reliably.

### D. Agent-owned room client with optional local observer

Every provider gets a small room client that maintains:

- durable `last_observed_seq` and `last_spoken_seq`;
- unread semantic events;
- roster and active-speaker state;
- safe attachment handles and cached thumbnails;
- scheduled wakeups and unresolved conversational obligations.

A deterministic gate is sufficient initially. Later, an optional local text
classifier or local VLM can rank whether the agent should pay attention without
charging each remote provider. This local observer must be measured rather than
described as free: it consumes host CPU/GPU and can still make poor decisions.

### E. Agent self-yield and delayed follow-up

After speaking, a structured adapter may emit hidden control metadata separate
from the visible message:

- `yield`: do not seek another immediate turn;
- `invite_next(participant_id)`: nominate another speaker;
- `follow_up_if_silent(after_seconds)`: schedule one durable timer;
- `wait_for(topic_or_participant)`: register an interest;
- `conversation_complete`: end the current chain.

The server owns enforcement and timers. No repeated model or room polling is
needed. Natural visible output remains plain text.

## Human-Like Turn Rules

Conversation-analysis research distinguishes two important cases:

1. The current speaker selects the next speaker, for example by name, question
   or reply relationship.
2. No next speaker is selected, so participants may self-select or nobody may
   speak.

That second outcome matters. Recent multi-party dialogue research reports that
models do not reliably learn "speak or stay silent" from ordinary prompting,
and next-speaker models can overpredict one participant. The room needs an
explicit silence outcome and fairness constraints rather than assuming the
provider will infer them.

## Media Participation Contract

Media should follow the same canonical room event and attention path as text.

1. A message event references immutable `media_id` values.
2. The agent-owned client receives safe metadata immediately but no local path.
3. The attention decision can use cheap metadata: sender, reply target, MIME,
   filename, caption and explicit mention.
4. If selected, a provider capability adapter obtains the media through an
   authenticated, short-lived room handle.
5. The adapter passes it in the provider's native form: image content block,
   OpenCode attachment, Gemini multimodal file input, or another documented
   mechanism.
6. Unsupported media is reported as unsupported; it is never summarized as if
   viewed.

Images should not be converted to text globally before selection. That would
pay a vision/OCR cost for every attachment even when no agent needs it. A shared
thumbnail/OCR cache can be an optional optimization after measurement. Audio and
video need explicit staged adapters such as metadata, transcript and selected
frames; support must be capability based, not guessed from the provider name.

## Recommended Architecture

Use one canonical WebSocket and add an event-driven `AttentionCoordinator`
between room append and provider turn assignment.

```text
RoomStore append
    -> WebSocket broadcast to humans and agent-owned room clients
    -> short burst coalescer (event-triggered timer, not polling)
    -> deterministic candidate filter
    -> zero-or-one attention lease
    -> optional selector only for an ambiguous tie
    -> selected provider receives cursor diff + selected native media
    -> visible message plus optional hidden yield/follow-up intent
    -> durable cursor and timer update
```

Default policy:

- A human message can wake one relevant agent, not every default responder.
- An agent message does not automatically force another agent response.
- Mentions are public next-speaker selection, not private messages.
- A room-wide question allows self-selection, but at most one agent gets the
  first lease.
- A lease may expire without a provider call if the candidate is paused, over
  budget or no longer relevant.
- Once a provider turn is assigned, it must produce a real visible response or
  a structured failure. Blank visible messages are never control flow.
- Durable chain, rate, consecutive-turn and token budgets stop runaway rooms.

## Experiments

### Experiment 1: deterministic attention

Replay recorded room events without calling providers. Compare current routing
with mention/reply/question/nomination/cooldown rules.

Measure:

- provider turns avoided;
- incorrect wakeups;
- missed expected replies;
- speaker distribution;
- chains stopped with zero selected speakers.

### Experiment 2: structured blocking inbox

Implement only for one provider with a structured protocol. Leave the session
blocked for 30 minutes, wake it with text, image and a delayed timer, then verify
the same provider session resumes without polling or idle token use.

### Experiment 3: agent-owned client versus browser watcher

Give the same conversation to:

- the semantic room client;
- an on-demand accessibility snapshot;
- an on-demand screenshot.

Measure total input tokens, wake-to-first-output latency, message/identity
accuracy and image understanding. Do not run a continuous screenshot loop.

### Experiment 4: optional selector

Run a small/local selector only when deterministic rules leave two or more
plausible candidates. It must be allowed to return `nobody`. Compare it with
round robin and random selection, and cap the selector context independently.

## Acceptance Metrics

- idle remote-provider calls: 0;
- idle remote-provider input/output tokens: 0;
- room polling by model: 0;
- blank final messages: 0;
- media falsely reported as viewed: 0;
- at most one initial speaker lease per ordinary event burst;
- reconnect resumes from durable sequence without full transcript replay;
- configurable room and per-agent token/reply budgets are enforced;
- agent can remain silent, nominate a peer, or schedule one follow-up without a
  visible protocol marker.

## Decision

Do not replace the current forced alternation with an LLM selector alone. First
introduce the ability to select nobody and keep provider sessions asleep. The
best first slice is an event-driven agent-owned room client plus deterministic
attention leases and durable timers. Test the blocking inbox as an opt-in path
for structured providers, and use browser/screenshot observation only for UI
validation or on-demand visual context.

## Primary References

- Discord Gateway event delivery: https://docs.discord.com/developers/events/gateway
- AutoGen SelectorGroupChat: https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/selector-group-chat.html
- Microsoft Agent Framework group-chat orchestration: https://learn.microsoft.com/en-gb/agent-framework/workflows/orchestrations/group-chat
- LangChain handoffs and context engineering: https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
- Sacks, Schegloff and Jefferson turn allocation: https://pure.mpg.de/rest/items/item_2376846/component/file_2376845/content
- Speak or Stay Silent benchmark: https://arxiv.org/abs/2603.11409
- Multi-party next-speaker prediction: https://aclanthology.org/2026.iwsds-1.8/
- Playwright accessibility snapshots: https://playwright.dev/agent-cli/snapshots
- Claude computer-use screenshot loop: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- Claude vision input: https://platform.claude.com/docs/en/build-with-claude/vision
- Gemini CLI multimodal file input: https://geminicli.com/docs/cli/custom-commands/
- OpenCode image attachments: https://dev.opencode.ai/docs/config
