# Frontend Feature Matrix

Status: current product inventory and verification record

Last updated: 2026-08-21

Read when: changing the React room client, deciding whether a frontend feature
is implemented, or planning a release smoke. This document records observed
behavior; it does not replace the owning code or its tests.

## Status Legend

- **Live verified**: exercised through the running browser UI and observed at
  the user-visible boundary.
- **Surface verified**: the UI and guard state were exercised, but a destructive,
  credential-bearing, external, or unavailable final action was not performed.
- **Automated only**: covered by focused tests, but not completed in the 2026-08-21
  live smoke.
- **Needs work**: the live workflow exposed a missing or misleading behavior.
- **Not verified**: the current smoke could not establish the behavior.

## Identity And Startup

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Startup identity gate | `views/components/StartupIdentityGate.tsx` | Google, new guest, and recovery choices rendered in the installed app and browser | Live verified | Full Google exchange still needs a configured central directory release smoke |
| New guest identity | `views/components/GuestIdentityRecoveryPanel.tsx`, `lib/guestRecovery.ts` | Created a guest, displayed the recovery step, acknowledged it, and entered the application | Live verified | Do not expose recovery material in logs or screenshots |
| Existing recovery code | `views/components/GuestRecoverySettings.tsx`, `lib/centralIdentity.ts` | Entry surface and automated recovery coverage exist | Automated only | Run a clean-device recovery smoke with disposable identity data |
| Google account link | `views/components/GoogleAccountSettings.tsx`, `lib/googleIdentity.ts` | Account settings correctly reported that the isolated server had no Google configuration | Surface verified | Verify cancel, failure, account selection, callback, and relaunch against a configured non-production directory |
| Startup loading experience | `views/components/StartupIdentityBoundary.tsx` | No obsolete local-room loading interstitial appeared in the current flow | Live verified | Recheck in the signed desktop build after every startup-flow change |

## Rooms, Channels, And Navigation

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Create and enter a room | `App.tsx`, `app/useRoomDirectory.ts` | Created and entered a disposable room | Live verified | Add a repeatable release fixture so this does not depend on manual setup |
| Room metadata and appearance | `views/components/RoomSettingsModal.tsx`, `app/useRoomSettingsController.ts` | Changed name, topic, icon, theme, mode, tool mode, and notification scope; restored defaults where appropriate | Live verified | Add visual checks for all themes and narrow layouts |
| Reload and durable hydration | `app/useRoomDirectory.ts`, `lib/roomDockPersistence.ts`, `useCanonicalRoom.ts` | Reloaded the browser and recovered the room, custom channel, provider roster, and transcript | Live verified | Exercise reconnect with an intentional sequence gap and server restart |
| Custom text channel | `views/components/CreateChannelModal.tsx`, `views/CustomChannelView.tsx` | Created `qa-검증`, entered it, sent a message, and observed it | Live verified | Voice-channel creation is only a labelled preparation surface |
| Voice channel | `views/components/CreateChannelModal.tsx` | Voice type was selectable and described as audio preparation | Surface verified | Implement a real media/session contract before presenting it as working voice chat |
| Channel context menu | `views/components/ChannelContextMenu.tsx` | Computer Use secondary click produced Chrome's native menu, not conclusive app evidence | Not verified | Add a keyboard-accessible trigger and browser E2E coverage |
| Friends and DM directory | `views/FriendsView.tsx`, `app/useFriendsDirectory.ts` | Online/all filters, search, friend-add form, categories, and empty states rendered | Surface verified | Verify saved friend, DM exchange, and previous-session import with two disposable identities |
| Member list and right panel | `views/components/MemberList.tsx`, `RoomConnectionPanel.tsx` | Hid/restored the member panel; opened member details and provider controls | Live verified | Verify every owner/guest moderation combination with a multi-client fixture |

## Messaging And Collaboration

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Room message send/render | `views/LobbyView.tsx`, `views/components/LobbyComposer.tsx` | Sent messages and observed ordered transcript rendering | Live verified | Repeat with a remote guest and reconnect during send |
| Mentions and emoji | `views/components/MentionInput.tsx`, `lib/mentionComposerModel.ts`, `views/components/LobbyComposer.tsx` | Mention insertion remained available; the emoji picker opened and inserted a selected emoji into the live composer | Live verified | Verify keyboard selection across a long roster and the emoji grid |
| Gift, GIF, sticker helpers | `views/components/LobbyComposer.tsx` | The misleading placeholder controls were removed; they no longer appear in the live composer | Live verified | Add rich-media integrations only with a real transport and product contract |
| App command menu and vote entry | `views/components/ComposerCommandMenu.tsx`, `VoteComposerDialog.tsx` | App control opened the `/vote` command choice | Surface verified | Complete a live poll create/vote/close smoke |
| Attachments | `views/components/LobbyAttachments.tsx` | Native file chooser opened and cancellation returned cleanly | Surface verified | Upload, render, download, size/type rejection, and reconnect need a disposable-file smoke |
| Channel search | `views/components/ChannelHeader.tsx`, `views/LobbyView.tsx`, `views/CustomChannelView.tsx` | `Cmd+F` opened app search instead of browser find; a live query produced eight results and keyboard selection focused the original message | Live verified | Replace loaded-history filtering with the accepted indexed server-side search contract |
| Older room history | `views/lobby/useLobbyHistory.ts`, `roomSocketClient.ts` | A 450-message room initially loaded the latest 200; upward scrolling fetched older pages and preserved a usable reading position. A live long-room check showed the non-layout-shifting old-history notice above the composer and returned to latest; automated coverage proves load errors remain visible and retryable | Live verified | Add a repeatable browser fixture that measures anchor position across each individual page prepend |
| Per-channel read cursor | `views/LobbyView.tsx`, `app/useRoomSettingsController.ts`; server owner `room/user_preferences.py` | The live unread bar moved to the first unread event, marking read did not move the timeline, and the exact event-sequence cursor remained read after browser reload | Live verified | Advance automatically only from focused, actually viewed messages; repeat across two devices for the same identity |
| Pinned messages | `views/components/ChannelHeader.tsx` | Toggle opened the empty pinned-message state and closed cleanly | Surface verified | Pin/unpin requires a real message action and persistence check |
| Side chat | `views/components/SideChatDock.tsx`, `app/useRoomSideChat.ts`; server owner `features/side_chat/service.py` | Sent a message through the running browser UI, observed it render, restarted the server, and confirmed the side chat returned empty; automated checks cover room isolation, retention, read-only UI, and Agent Bridge rejection | Live verified | Repeat send/view behavior with two human browser participants |
| Main-message threads | Removed from the active product surface | Main-message buttons, thread tabs, thread state, and the obsolete frontend thread model were removed | Intentionally absent | Keep side chat independent unless a new thread product is explicitly designed |

## Agents And Providers

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Add DeepSeek API agent | `views/components/AgentCreateModal.tsx`, `api/agentSessions.ts` | Created and started `deepseek-v4-flash`; direct mention produced a live reply ending in the requested marker | Live verified | Use a disposable credential in CI or a gated release smoke |
| Add Grok CLI agent | same as above plus provider runtime | Canonical create path started the real Grok CLI; direct mention produced the requested live reply | Live verified | Browser workspace picker prevented completing creation solely through the form |
| Add OpenCode free Hy3 agent | same as above plus provider runtime | Selected exact free model `opencode/hy3-free`; real OpenCode server replied to a direct mention | Live verified | Keep free and paid Hy3 labels/pricing visibly distinct |
| Ordered routing and decline | room transport and provider bridges | An agent not directly addressed declined through the structured room path; direct mentions reached the intended agent | Live verified | Add deterministic cross-provider routing smoke fixtures |
| Provider member details | `views/components/member/MemberDetailModal.tsx`, `AgentSessionMemberDetails.tsx` | Profile, runtime settings, visibility, session controls, diagnostics, moderation, and usage surfaces rendered | Surface verified | Pause/resume did not yield conclusive live UI evidence; stop/restart and kick were intentionally not performed |
| Workspace picker | `views/components/WorkspacePickerField.tsx` and `providers/workspace_picker.py` | The macOS picker appeared in front as a Finder-owned dialog; cancel returned to an enabled form, and focused coverage prevents raw backend codes reaching the user | Live verified | Complete one workspace-bound agent creation from folder selection through provider start |

## Invites, Settings, And Platform Surfaces

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Room invite modal and scopes | `views/components/RoomInviteModal.tsx`, `app/useRoomInviteController.ts` | Person invites expose 1/5/unlimited use limits and 1-hour/24-hour/7-day expiry choices; external AI invites state their fixed one-use, one-hour lifetime | Live verified | Exercise read/write person admission with a second disposable browser session |
| Public invite tunnel | invite controller and backend tunnel service | Invite generation while local showed an explicit confirmation, then opened a Quick Tunnel only after consent; switching external access off cleared the public route | Live verified | Repeat failure and cancellation states when cloudflared is unavailable |
| External AI session invite | `RoomInviteModal.tsx`, Room Connector admission | A disposable one-use link was sent to a ChatGPT web temporary chat; ChatGPT correctly reported that no `room_join`/Room Connector tool was available, while the server retained one unused pending invite with zero sessions and zero consumed nonces | Live verified | Verify success with a supported Codex or Claude client that has the Room Connector registered |
| Destructive room actions | `RoomSettingsModal.tsx`, `LeaveRoomDialog.tsx` | Confirmation guards rendered | Surface verified | Final delete/leave was intentionally not exercised |
| Account and profile settings | `views/components/UserSettingsPanel.tsx` | Account, profile, avatar/banner/status, presence, and save/revert surfaces rendered | Surface verified | Verify profile persistence and conflict/error behavior with a second client |
| Audio controls | `views/components/UserPanel.tsx`, `UserSettingsPanel.tsx` | UI explicitly states that mute/headset are display state only | Surface verified | Do not describe these controls as real voice until media transport exists |
| Desktop wrapper | `desktop/src-tauri/` | Signed app launched to the current startup gate during this verification session | Surface verified | Run the same room/provider smoke inside the installed desktop wrapper |
| Mobile room panel | `views/components/MobileRoomInfoPanel.tsx` | Focused automated tests exist | Automated only | Manual narrow-screen and real mobile-wrapper verification are still required |

## 2026-08-21 Live Smoke Boundary

The smoke used isolated local data roots and temporary rooms. It exercised
real DeepSeek, Grok, and OpenCode provider processes, plus a 450-message history
fixture for paging and search. It did not publish a
release, retain a public tunnel, delete durable user data, link a real Google
account, send user files, or expose recovery, invite, or credential material.

The live smoke is strong evidence for the paths named **Live verified**. It is
not a claim that every browser, viewport, permission combination, failure mode,
or multi-user race has been exhausted.
