# Human-Maintainable Code

Use these instructions as general coding guidance for any project in this repository.

The goal is not to make code look architecturally impressive. The goal is to make the next safe change easy for a human to discover, understand, implement, verify, and review.

## Project Product Memory

Before changing live-agent, provider, memory, meeting-record, or GUI behavior,
read `docs/product/OPERATING_MODEL.md` together with the closest topic doc:
`docs/live-session-room-model.md`, `docs/provider-architecture.md`,
`docs/live-agent-ops.md`, or `docs/roadmap.md`.

`docs/product/OPERATING_MODEL.md` records current product boundaries such as
discovery not being execution, real provider CLIs requiring explicit approval,
agent-private context staying provider-owned, shared meeting memory staying
AgentsAssemble-owned, and Work Mode / Play Mode remaining separate unless an
explicit promote action is designed.

## Operating Summary

Before changing code, understand the local pattern and the reason for change.
Optimize for the next human maintainer, not for line count, file count, or architectural appearance.
Keep same-reason-to-change code together; split only when responsibility, failure mode, validation path, ownership, lifetime, or side effect differs.
During refactors, preserve behavior and verify in small steps.
Keep changes scoped; do not touch unrelated code or user-owned dirty work.
Do not hide uncertainty: if the request, current behavior, or success criteria are unclear, surface the ambiguity before coding.
Prefer the smallest verifiable change that satisfies the request; avoid speculative features, flexibility, or abstractions.

## Core Standard

Optimize for human-maintainable product code.

Good code should make these questions easy to answer:

- Where should I make this change?
- What behavior might this change affect?
- What state or side effect does this code own?
- What can I test to know I did not break it?
- Can I return to this code later and recover the intent quickly?

Do not optimize primarily for:

- Lower line count.
- More files.
- Fewer files.
- More abstraction.
- A design pattern for its own sake.
- Architecture that looks clean but hides coupling.

Line count is a signal, not a goal. Reducing lines is good when it removes duplication, clarifies responsibility, improves locality, or deletes dead code. Reducing lines is bad when it scatters context, hides control flow, creates shapeless helpers, or makes readers jump across files without a clear reason.

## Intent And Scope Discipline

Before implementing, identify the user's requested outcome and the evidence that will show it worked.

If multiple interpretations are plausible, do not silently choose the most convenient one. State the ambiguity and either ask or choose the smallest reversible path that preserves the user's intent.

Prefer simple solutions over impressive ones. Do not add features, configurability, generalized frameworks, or defensive handling for scenarios the product does not actually need yet.

Every changed line should trace back to the user's request, a required verification path, or cleanup made necessary by your own change. If unrelated dead code, formatting problems, or cleanup opportunities appear, mention them separately instead of folding them into the current change.

For multi-step work, keep the working goal narrow enough to verify. A good goal names the behavior being changed, the non-goals, the allowed side effects, and the checks that must pass.

## Organization Principles

Keep code together when it has the same reason to change.

Split code when one or more of these differs:

- Responsibility.
- Failure mode.
- Validation or test path.
- Platform boundary.
- Ownership.
- Data lifetime.
- User-visible behavior.
- Side-effect boundary.

A long coherent file can be better than many tiny files. A short file can still be bad if it hides coupling or forces readers to chase context.

Prefer names that explain the responsibility, not the implementation trick. A file or type name should help a future reader predict what belongs there and what does not.

Prefer local conventions over general taste. Before introducing a new pattern, inspect the nearest existing implementation that solves a similar problem. Use the project's established style unless it is clearly harmful or the user explicitly asks to change direction.

Keep changes scoped to the requested behavior. Do not reformat, rename, reorder, or refactor unrelated code while touching a file. If nearby cleanup is useful but not required, leave it for a separate change.

Assume uncommitted changes may belong to the user or another tool. Do not revert, overwrite, normalize, or move them unless explicitly asked. If they conflict with the task, explain the conflict and choose the smallest safe path forward.

## Roles

Use these role boundaries as defaults:

- Coordinators coordinate state and delegate work.
- Services perform focused work.
- Views render state and expose user actions.
- Models describe domain data and invariants.
- Policies decide behavior from inputs.
- Adapters isolate platform, framework, filesystem, network, process, or UI side effects.

Do not let one object quietly become responsible for coordination, persistence, rendering, policy, platform calls, parsing, diagnostics, and user interaction at the same time.

## Refactoring Rules

During a refactor, preserve behavior unless the user explicitly asks for behavior change.

Preserve:

- User-visible UI.
- Copy/text.
- Data formats.
- Persistence and schema.
- Public interfaces.
- Dependencies.
- Accessibility behavior.
- Error behavior.
- Existing tests' intended meaning.

Do not mix refactoring with redesign. If UI behavior or appearance must change, make that a separate, explicit change.

Prefer small verified steps. A good refactor can be reviewed as a series of boring, safe moves.

## Abstraction Rules

Do not abstract merely to appear clean.

Introduce an abstraction when it does at least one of these:

- Removes meaningful duplication.
- Names a domain concept.
- Isolates a side effect.
- Makes testing easier.
- Reduces the amount of code a reader must understand at once.
- Creates a stable boundary for likely future work.

Avoid abstractions that:

- Hide simple code behind vague names.
- Create pass-through layers.
- Separate code that always changes together.
- Require readers to open many files to understand one behavior.

Before keeping a new abstraction, ask:

- Can a reader predict what belongs behind this boundary?
- Does this boundary reduce future change risk?
- Would the next likely change touch both sides anyway?

## Constants And Hardcoding

Hardcoded values are not all equally bad.

Keep obvious local values local when naming them would add no meaning. Name values when the name explains intent, domain meaning, layout role, protocol meaning, or risk.

Good constants explain why a value exists. Bad constants only move a number somewhere else.

## Dependencies

Do not add dependencies for small convenience.

Add a dependency only when it replaces meaningful complexity, is maintained, fits the project, and the tradeoff is worth the install, runtime, security, licensing, and maintenance cost.

Prefer standard library, platform APIs, or already-installed project dependencies when they are a reasonable fit.

## Deletion And Compatibility

Delete code only when there is evidence it is unused, obsolete, or replaced.

Be especially careful with:

- Public APIs.
- CLI commands and flags.
- Configuration keys.
- File formats.
- Database schemas and migrations.
- User workflows.
- Error messages that tests or users may rely on.
- Persistence, import, export, and backup behavior.

Preserve compatibility unless the user explicitly permits a breaking change.

## Platform And Sample Code

Official sample code is useful for API usage, platform conventions, and framework intent. Treat it as guidance for how a platform expects an API to be used, not as a complete architecture template for a real product.

Mature open-source product code is useful for long-term maintainability patterns. Adapt it to the project instead of copying its shape blindly.

## Verification

After each meaningful change, run the cheapest reliable check that matches the risk:

- Targeted tests for local logic.
- Full tests for shared behavior.
- Build/typecheck for structural changes.
- App run or smoke test for integration changes.
- Visual inspection for UI changes.
- Diff/check scripts for formatting or packaging.

Do not claim done, fixed, verified, release-ready, signed, notarized, or final without direct evidence.

If a check fails, do not hide it. Report the exact command, the failure summary, and whether it appears related to the change. Do not claim success because unrelated checks passed.

## Commit And Push Discipline

Commit only when the current change is coherent, reviewed enough to explain, and verified at the level appropriate to its risk.

Prefer commits that:

- Have one clear reason to exist.
- Separate refactoring from behavior changes.
- Separate UI changes from logic changes when practical.
- Preserve a clean build/test boundary.
- Can be reverted without taking unrelated work with it.

Before committing:

- Inspect `git status`.
- Inspect the diff or summary.
- Run the relevant verification.
- Make sure unrelated or user-owned dirty work is not included.

Do not commit:

- Unrelated formatting churn.
- Temporary debug code.
- Generated debris unless it is an intended artifact.
- User changes you did not make, unless explicitly asked.

Push only when explicitly requested by the user.

Before pushing:

- Confirm the target branch and remote.
- Make sure local commits are intentional.
- Make sure no secrets, credentials, private data, or accidental large artifacts are included.

Open PRs, draft PRs, release notes, public comments, packages, deployments, or externally visible releases only when explicitly requested.

Never force-push, rewrite shared history, publish packages, deploy, or create externally visible releases without explicit approval.

## Side Effects And Approval

Ask before destructive, externally visible, credential-related, production, payment, messaging, publishing, or irreversible actions.

This includes:

- Deleting user data.
- Rewriting history.
- Force-pushing.
- Publishing packages.
- Deploying to production.
- Sending emails, messages, notifications, or PR comments.
- Changing secrets, tokens, credentials, permissions, billing, or account settings.
- Running migrations or scripts against production data.

Prefer dry runs, previews, local checks, or draft outputs before irreversible actions.

## Comments

Use comments to explain intent, constraints, surprising decisions, or external requirements.

Do not add comments that merely repeat what the code says. If code needs a comment to explain ordinary control flow, first consider whether the code can be named or structured more clearly.

## Reporting

When reporting work, separate:

- What changed.
- What stayed intentionally unchanged.
- What was verified.
- What remains unverified or blocked.
- What commit, if any, records the work.

## Starting A New Project Or Session

When starting a new project or handing work to a new agent/session, provide a short startup brief rather than relying on repository files alone.

Use the roadmap as product direction and priority guidance, not as permission to implement everything listed there.

The brief should include:

- The immediate goal or first slice.
- The source-of-truth files to read first.
- Explicit non-goals.
- Allowed side effects, including whether commits are allowed.
- Verification expectations.
- The next decision point where the agent should stop or report back.

Do not ask an agent to "follow the roadmap" without assigning a narrow next slice.
