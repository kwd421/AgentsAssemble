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
| Mentions and emoji | `views/components/MentionInput.tsx`, `lib/mentionComposerModel.ts` | Mention and emoji controls inserted content in the composer | Live verified | Verify keyboard selection across a long roster |
| Gift, GIF, sticker helpers | `views/components/LobbyComposer.tsx` | Each control inserted its local descriptive format and displayed the local-only explanation | Live verified | Clarify that these are text descriptions, not rich Discord media |
| App command menu and vote entry | `views/components/ComposerCommandMenu.tsx`, `VoteComposerDialog.tsx` | App control opened the `/vote` command choice | Surface verified | Complete a live poll create/vote/close smoke |
| Attachments | `views/components/LobbyAttachments.tsx` | Native file chooser opened and cancellation returned cleanly | Surface verified | Upload, render, download, size/type rejection, and reconnect need a disposable-file smoke |
| Channel search | `views/components/ChannelHeader.tsx` | Search accepted a known transcript token but left every message visible and showed only status copy | Needs work | Implement actual match filtering/navigation and empty-result behavior |
| Pinned messages | `views/components/ChannelHeader.tsx` | Toggle opened the empty pinned-message state and closed cleanly | Surface verified | Pin/unpin requires a real message action and persistence check |
| Side chat | `views/components/SideChatDock.tsx`, `app/useRoomSideChat.ts` | Sent a side-chat message and observed it in the unofficial panel | Live verified | Verify privacy/projection with a second participant |
| Threads | `views/components/SideChatDock.tsx`, `lib/sideChatThreadModel.ts` | Empty state correctly required opening a thread from a room message | Surface verified | No message affordance was conclusively found; opening and replying to a real thread remains unverified |

## Agents And Providers

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Add DeepSeek API agent | `views/components/AgentCreateModal.tsx`, `api/agentSessions.ts` | Created and started `deepseek-v4-flash`; direct mention produced a live reply ending in the requested marker | Live verified | Use a disposable credential in CI or a gated release smoke |
| Add Grok CLI agent | same as above plus provider runtime | Canonical create path started the real Grok CLI; direct mention produced the requested live reply | Live verified | Browser workspace picker prevented completing creation solely through the form |
| Add OpenCode free Hy3 agent | same as above plus provider runtime | Selected exact free model `opencode/hy3-free`; real OpenCode server replied to a direct mention | Live verified | Keep free and paid Hy3 labels/pricing visibly distinct |
| Ordered routing and decline | room transport and provider bridges | An agent not directly addressed declined through the structured room path; direct mentions reached the intended agent | Live verified | Add deterministic cross-provider routing smoke fixtures |
| Provider member details | `views/components/member/MemberDetailModal.tsx`, `AgentSessionMemberDetails.tsx` | Profile, runtime settings, visibility, session controls, diagnostics, moderation, and usage surfaces rendered | Surface verified | Pause/resume did not yield conclusive live UI evidence; stop/restart and kick were intentionally not performed |
| Workspace picker | `views/components/WorkspacePickerField.tsx` and native picker route | Browser form remained at `선택 중...`; terminating the stuck chooser exposed raw `workspace_picker_failed` | Needs work | Make the picker appear reliably, add cancel/timeout recovery, and present a human error message |

## Invites, Settings, And Platform Surfaces

| Capability | Current owner | Evidence | Status | Remaining gap |
| --- | --- | --- | --- | --- |
| Room invite modal and scopes | `views/components/RoomInviteModal.tsx`, `app/useRoomInviteController.ts` | Modal, read-only scope, connector details, advanced settings, and local preview rendered | Surface verified | Exercise read/write admission with a second disposable browser session |
| Public invite tunnel | invite controller and backend tunnel service | Creating an AI invite opened a Quick Tunnel and produced a link; switching back to local closed it | Needs work | Require an explicit public-exposure confirmation before starting a tunnel and show its lifetime/state clearly |
| Destructive room actions | `RoomSettingsModal.tsx`, `LeaveRoomDialog.tsx` | Confirmation guards rendered | Surface verified | Final delete/leave was intentionally not exercised |
| Account and profile settings | `views/components/UserSettingsPanel.tsx` | Account, profile, avatar/banner/status, presence, and save/revert surfaces rendered | Surface verified | Verify profile persistence and conflict/error behavior with a second client |
| Audio controls | `views/components/UserPanel.tsx`, `UserSettingsPanel.tsx` | UI explicitly states that mute/headset are display state only | Surface verified | Do not describe these controls as real voice until media transport exists |
| Desktop wrapper | `desktop/src-tauri/` | Signed app launched to the current startup gate during this verification session | Surface verified | Run the same room/provider smoke inside the installed desktop wrapper |
| Mobile room panel | `views/components/MobileRoomInfoPanel.tsx` | Focused automated tests exist | Automated only | Manual narrow-screen and real mobile-wrapper verification are still required |

## 2026-08-21 Live Smoke Boundary

The smoke used an isolated local data root and a temporary room. It exercised
real DeepSeek, Grok, and OpenCode provider processes. It did not publish a
release, retain a public tunnel, delete durable user data, link a real Google
account, send user files, or expose recovery, invite, or credential material.

The live smoke is strong evidence for the paths named **Live verified**. It is
not a claim that every browser, viewport, permission combination, failure mode,
or multi-user race has been exhausted.
