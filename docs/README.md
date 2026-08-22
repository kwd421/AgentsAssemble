# AgentsAssemble Documentation Map

Use documents by role so current behavior, future direction, execution plans,
and historical evidence do not compete as separate sources of truth.

| Role | Authority |
| --- | --- |
| Current product and architecture | `product/CURRENT_SYSTEM.md` |
| Detailed security, context, record, and mode policy | `product/OPERATING_MODEL.md` |
| Sole active product roadmap | `roadmap.md` |
| Current frontend inventory and verification | `product/FRONTEND_FEATURE_MATRIX.md` |
| Narrow implementation plans | `plans/` and current topic documents |
| Smoke results, audits, and research evidence | `reports/`, `superpowers/audits/`, and `research-log.md` |
| Legacy implementation/operator references | documents explicitly marked legacy or superseded |
| Historical v0.1 release bar | `product/V0_1_RELEASE_CHECKLIST.md` |

Detailed topic routing and ownership live in the documentation map near the end
of `product/CURRENT_SYSTEM.md`.

## Rules

- Do not create another roadmap or master-plan document. Update `roadmap.md`.
- A dated plan must name its status, narrow scope, owner boundary, exit evidence,
  and what current document it may update when finished.
- Move implemented facts to the current-system or evidence document; do not leave
  them described only as future work.
- Reports and historical plans are evidence, not implementation authority.
- When documents conflict, verify the code and behavior, then reconcile the
  current documents instead of choosing whichever is convenient.
