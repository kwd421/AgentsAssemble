# Security scan a592c155 — fix report

- Original scan revision: `a592c155420f42c0d3707d4b9cce1c35074ca7b7`
- Review date: 2026-08-11
- Fix branches/commits: `e0259cfb`, `271c98ea`, `80444d79`
- Scope: the ten findings in the supplied Codex Security scan
- Full-suite status: intentionally not run, per user direction

## Result

All ten supplied findings have a code-level remediation and a regression check at
the owning boundary. The targeted Python bundle completed **99/99** and the
stored-Markdown frontend check completed **5/5**.

This report does not claim a new independent security scan found no additional
issues. It records remediation and targeted verification of the supplied ten
findings.

## Finding-by-finding disposition

### 1. Revoked operator pairings retained descendant authority — fixed

Pairing-derived authority now carries revocable lineage. Recovery, account
linking, and durable credential creation fail closed for revoked or ineligible
pairing-derived sessions, while the host's independent operator authority
remains valid.

- Fix: `e0259cfb security: fail closed on pairing lineage and membership scope`
- Evidence: `tests/test_public_invite_http.py`
- Verification: descendants created through recovery/device/Google paths are
  rejected after revocation; the local host session remains authorized.

### 2. Antigravity approved a transformed command — fixed

Approval validates the exact command bytes. Newlines, over-limit input,
canonicalization changes, shell expansions, and symlinked configuration targets
are rejected instead of approving a cleaned prefix while executing the original.

- Fix: `271c98ea security: harden room, websocket, file, and provider boundaries`
- Evidence: `tests/test_antigravity_provider_hooks.py`
- Verification: multiline/suffix variants and unsafe workspace hook paths fail
  closed; only the exact allowed helper command is accepted.

### 3. Long-lived public WebSockets exhausted HTTP workers — fixed

WebSocket upgrades no longer consume the short-request worker reserve for their
entire lifetime, and public socket admission is bounded independently.

- Fix: `271c98ea`
- Evidence: `tests/test_ws_endpoint.py`
- Verification: public socket capacity cannot consume the operator HTTP reserve,
  and excess sockets are rejected at admission.

### 4. Read-only Agent Bridges published terminal mutations — fixed

Bridge startup/turn terminal mutations are admitted through the same room
command policy as other writes. Read-only observation bridges cannot publish
`start_failed`, decline, completion, or failure terminal records.

- Fix: `271c98ea`
- Evidence: `tests/test_room_command_admission.py`
- Verification: read-only bridge mutations are rejected before persistence and
  public projection.

### 5. Legacy read-only memberships migrated to read-write — fixed

Migration preserves the original invite scope when it is known. Ambiguous
legacy memberships are quarantined/audited instead of silently gaining write
recovery authority.

- Fix: `e0259cfb`
- Evidence: `tests/test_membership_scope_migration.py`
- Verification: read-only rows remain read-only after migration and recovery;
  ambiguous rows do not become writable.

### 6. Stored Markdown images caused viewer-side requests — fixed

Untrusted Markdown image syntax is rendered without creating an active remote
image request. Ordinary links and allowed media projection remain separate.

- Fix: `271c98ea`
- Evidence: `frontend/src/views/components/DiscordText.test.tsx`
- Verification: five focused frontend cases pass, including the stored-image
  request regression.

### 7. Owner-only Agent Session activity became public — fixed

Private activity is removed before construction of public room events. Unknown
activity does not default to a public audience.

- Fix: `271c98ea`
- Evidence: `tests/test_room_projection.py`
- Verification: owner-only reasoning/tool activity is absent from another
  participant's projection while explicitly public activity remains visible.

### 8. Workspace listing/search accepted unbounded input — fixed

Listing/search impose request, traversal, result, and payload budgets and reject
oversized work before filesystem allocation or provider continuation.

- Fix: `271c98ea`
- Evidence: `tests/test_api_work_harness_security.py`
- Verification: oversized queries and result sets fail at the tool boundary.

### 9. Symlinked workspace `.agents` redirected hook writes — fixed

Antigravity configuration preparation checks the target chain without following
a repository-controlled `.agents` symlink and fails closed on unsafe paths.

- Fix: `271c98ea`
- Evidence: `tests/test_antigravity_provider_hooks.py`
- Verification: a symlinked `.agents` directory cannot redirect generated hook
  material outside the selected workspace.

### 10. Legacy council role IDs traversed outside the meeting directory — fixed

Role identifiers are validated as safe path components before artifact paths are
formed.

- Fix: `271c98ea`
- Evidence: `tests/test_config.py`
- Verification: traversal and separator variants are rejected; valid role IDs
  remain usable.

## Additional adjacent verification

`80444d79 fix: require observation receipts and surface OpenCode usage`
closed two adjacent provider integrity gaps found while rechecking the scan
surface:

- ambient cursors advance only after evidence that the provider read the
  assigned state;
- OpenCode usage is reported from its real session data rather than inferred or
  silently omitted.

Evidence: `tests/test_cerebras_room_observation.py`,
`tests/test_deepseek_room_observation.py`,
`tests/test_local_openai_room_observation.py`,
`tests/test_openai_compatible_room_actions.py`,
`tests/test_opencode_usage.py`, and
`tests/test_room_agent_bridge_failures.py`.

## Commands run

```text
python3 -m unittest   tests.test_antigravity_provider_hooks   tests.test_api_work_harness_security   tests.test_config   tests.test_membership_scope_migration   tests.test_public_invite_http   tests.test_room_command_admission   tests.test_room_projection   tests.test_ws_endpoint   tests.test_cerebras_room_observation   tests.test_deepseek_room_observation   tests.test_gui_server_provider_http   tests.test_local_openai_room_observation   tests.test_openai_compatible_room_actions   tests.test_opencode_usage   tests.test_room_agent_bridge_failures
# Ran 99 tests — OK

cd frontend
npm test -- --run src/views/components/DiscordText.test.tsx
# 1 file, 5 tests — passed
```

Expected timeout diagnostics emitted by failure-path tests were visible during
the Python run; the test process still completed successfully.
