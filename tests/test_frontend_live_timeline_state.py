from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLiveTimelineStateTests(unittest.TestCase):
    def test_live_timeline_state_preserves_delta_identity_and_scroll_intent(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import { compileTypeScriptModule } from "./tests/frontend_api_runtime_helpers.mjs";

            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-live-timeline-state-"));
            const timelinePath = await compileTypeScriptModule(
              path.resolve("frontend/src/lib/liveTimelineState.ts"),
              tempDir,
            );
            const timeline = await import(pathToFileURL(timelinePath).href);

            const eventA = {
              id: "live-a",
              kind: "message",
              name: "Codex",
              side: "other-agent",
              message: "첫 발언",
              created_at: "2026-01-01T00:00:00Z",
            };
            const eventB = {
              id: "live-b",
              kind: "message",
              name: "Kiro",
              side: "other-agent",
              message: "두 번째 발언",
              created_at: "2026-01-01T00:00:01Z",
            };
            const eventC = {
              id: "live-c",
              kind: "message",
              name: "Grok",
              side: "other-agent",
              message: "세 번째 발언",
              created_at: "2026-01-01T00:00:02Z",
            };
            const previous = [eventA, eventB];

            const identicalRefresh = timeline.mergeLiveTimelineEvents({
              previousEvents: previous,
              incomingEvents: [{ ...eventA }, { ...eventB }],
              reset: false,
            });
            assert.equal(identicalRefresh, previous);
            assert.equal(identicalRefresh[0], eventA);
            assert.equal(identicalRefresh[1], eventB);

            const appended = timeline.mergeLiveTimelineEvents({
              previousEvents: previous,
              incomingEvents: [{ ...eventA }, { ...eventB }, eventC],
              reset: false,
            });
            assert.deepEqual(appended.map((event) => event.id), ["live-a", "live-b", "live-c"]);
            assert.equal(appended[0], eventA);
            assert.equal(appended[1], eventB);
            assert.equal(appended[2], eventC);

            const updated = timeline.mergeLiveTimelineEvents({
              previousEvents: appended,
              incomingEvents: [{ ...eventB, message: "두 번째 발언 수정" }],
              reset: false,
            });
            assert.deepEqual(updated.map((event) => event.id), ["live-a", "live-b", "live-c"]);
            assert.equal(updated[0], eventA);
            assert.notEqual(updated[1], eventB);
            assert.equal(updated[1].message, "두 번째 발언 수정");
            assert.equal(updated[2], eventC);

            const sameTimeB = { ...eventB, created_at: "2026-01-01T00:00:10Z" };
            const sameTimeA = { ...eventA, created_at: "2026-01-01T00:00:10Z" };
            const sameTimestampPrevious = [sameTimeB, sameTimeA];
            const sameTimestampRefresh = timeline.mergeLiveTimelineEvents({
              previousEvents: sameTimestampPrevious,
              incomingEvents: [{ ...sameTimeB }, { ...sameTimeA }],
              reset: false,
            });
            assert.equal(sameTimestampRefresh, sameTimestampPrevious);
            assert.deepEqual(sameTimestampRefresh.map((event) => event.id), ["live-b", "live-a"]);
            const sameTimestampReset = timeline.mergeLiveTimelineEvents({
              previousEvents: [],
              incomingEvents: [{ ...sameTimeB }, { ...sameTimeA }],
              reset: true,
            });
            assert.deepEqual(sameTimestampReset.map((event) => event.id), ["live-b", "live-a"]);

            assert.equal(
              timeline.liveTimelineResetReason({
                previousFlowId: "flow-1",
                nextFlowId: "flow-1",
                previousMeetingId: "m1",
                nextMeetingId: "m1",
                previousTimelineSource: "official",
                nextTimelineSource: "official",
              }),
              ""
            );
            assert.equal(
              timeline.liveTimelineResetReason({
                previousFlowId: "flow-1",
                nextFlowId: "flow-2",
                previousMeetingId: "m1",
                nextMeetingId: "m1",
                previousTimelineSource: "official",
                nextTimelineSource: "official",
              }),
              "flow"
            );
            assert.equal(
              timeline.liveTimelineResetReason({
                previousFlowId: "flow-1",
                nextFlowId: "flow-1",
                previousMeetingId: "m1",
                nextMeetingId: "m2",
                previousTimelineSource: "official",
                nextTimelineSource: "official",
              }),
              "meeting"
            );
            assert.equal(
              timeline.liveTimelineResetReason({
                previousFlowId: "flow-1",
                nextFlowId: "flow-1",
                previousMeetingId: "m1",
                nextMeetingId: "m1",
                previousTimelineSource: "official",
                nextTimelineSource: "flow",
              }),
              "source"
            );

            assert.equal(timeline.nextTimelinePinnedToLatest(false, ""), false);
            assert.equal(timeline.nextTimelinePinnedToLatest(true, ""), true);
            assert.equal(timeline.nextTimelinePinnedToLatest(false, "meeting"), true);

            const filteredForMeeting = timeline.filterFlowTimelineEvents({
              activeMeetingId: "m2",
              incomingEvents: [
                { id: "old", flow_event_type: "speak", flow_meeting_id: "m1", created_at: "1" },
                { id: "current", flow_event_type: "speak", flow_meeting_id: "m2", created_at: "2" },
                { id: "not-flow", kind: "message", flow_meeting_id: "m2", created_at: "3" },
              ],
            });
            assert.deepEqual(filteredForMeeting.map((event) => event.id), ["current"]);

            const filteredForFlow = timeline.filterFlowTimelineEvents({
              activeFlowId: "flow-active",
              activeMeetingId: "m2",
              incomingEvents: [
                { id: "wrong-flow", flow_action: "speak", flow_id: "flow-old", flow_meeting_id: "m2", created_at: "1" },
                { id: "stale-meeting", flow_action: "speak", flow_id: "flow-active", flow_meeting_id: "m1", created_at: "2" },
                { id: "right-flow", flow_action: "speak", flow_id: "flow-active", flow_meeting_id: "m2", created_at: "3" },
              ],
            });
            assert.deepEqual(filteredForFlow.map((event) => event.id), ["right-flow"]);
            """
        )
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
