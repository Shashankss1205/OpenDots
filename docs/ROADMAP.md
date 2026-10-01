# Remaining work after the 0.2.0 audit

The eight reproduced defects from the initial audit are fixed: symlink scope
bypass, incomplete approval context, damaged/truncated patches, hidden pending
reviews, unmatched manual dashboard events, comment normalization, stale desired
state, and installed first-run configuration. Subsequent PRs add repair, runtime
limits, terminal UX, explicit proposal acceptance, source refresh and maintenance.

These larger improvements remain open; they are not implied by the completed
local fixes or the CI results.

| Priority | Improvement | Acceptance criterion |
| --- | --- | --- |
| Before unattended real use | Fresh live Codex and prolonged recovery/soak runs | Reproducible successful model runs, bounded cost, process recovery and multi-day evidence |
| Before outbound actions | Approved GitHub publication | Preview repository/base/branch/diff; bind approval to exact remote effect; revalidate upstream; idempotent push/PR recovery |
| Before shared hosting | Authentication and tenancy | Authenticated principals, per-target authorization, credential separation and audit access; local headers are insufficient |
| Next onboarding pass | Guided target creation and editing | Terminal/browser setup for workspace, subscriptions, scopes and checks, validated before activation |
| Next UI pass | Browser history and proposal acceptance parity | Search/paginate old work and accept reviewed commits through the same existing APIs |
| High-volume operation | Event coalescing and stronger catch-up | Explicit merge semantics without losing distinct owner requests; durable gap reconciliation |
| Long-term storage | Database and Git archival | Export/verify old audit and event rows, preserve deduplication contracts, and safely prune unreachable objects |
| Continuous upstream work | Automatic synchronization and conflict handling | Observe upstream revisions, invalidate stale evidence, and reconcile accepted proposals without silent changes |
| Stronger planner boundary | Attested execution profile | Validate external CLI hooks/plugins/network behavior, dedicated credentials and process isolation |
| Budget precision | Provider usage and cost accounting | Authoritative per-call token/cost records and enforceable limits; current budgets count invocations |
| Deployment coverage | Nested container isolation and live systemd test | Real checks and planner access under supported deployment profiles, not only startup health |
| Distribution | Published release/PyPI and update flow | Maintainer-reviewed releases, verified upgrades/rollback and explicit package ownership |

Built-in runtime tools remain local file operations, owner-selected checks and
notes. Installing a trusted extension can add capabilities but does not make them
sandboxed or automatically safe. The runtime still has no general exactly-once
transaction spanning filesystem, subprocess and network effects.
