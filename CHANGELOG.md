# Unreleased

- Missing configurations explain how to initialize OpenDots without creating state.
- CLI help describes status, pause, resume, cancel and approval decisions.
- Development fixture commands select their configuration explicitly.
- Planner snapshots reserve a source-file inventory before adding file contents.
- Terminal reviews summarize file reads, check verdicts and plugin results while retaining the full audit evidence.

# OpenDots 0.2.1

This release brings the redesigned browser dashboard to PyPI.

- Light workspace, navy navigation, mint accents and responsive mobile layout.
- Live goal selector with the selected agent's incoming event, activity and review.
- Colored, escaped diffs for proposed changes and retained patches.
- Exact-token approvals with duplicate-decision prevention across review panels.
- Clear empty, paused and disconnected states; actions disable during connection loss.
- Browser coverage for approvals, rejection, retained patches, connection recovery,
  event escaping, navigation and narrow screens.
- One-command installer defaults to the PyPI release, with optional version pinning.
- Restored README logo and updated dashboard documentation with actual screenshots.

Stop the running OpenDots service before upgrading its Python environment, then
restart it to load the new dashboard. Existing configuration and runtime data
remain in their configured locations. The MIT license is unchanged.

# OpenDots 0.2.0

This release develops the local prototype with a prompt-first terminal client,
safer proposal handling, packaged setup, and runtime maintenance.

- Exact-action approvals bind check commands, workspace content and owner policy.
- Write scopes reject symlink bypasses and protect owner validation harnesses.
- Failed checks can trigger bounded repair; completion requires current evidence.
- Complete binary-capable Git patches are retained without preview truncation.
- Completed proposals require explicit acceptance before future tasks use them.
- Source synchronization creates new snapshots while preserving old proposals.
- Prompt-first terminal UI supports reviews, history, activity, cancellation,
  proposal acceptance, command completion and input editing.
- Packaged `init`, `doctor`, Bash installation and user systemd lifecycle commands.
- Non-root Docker deployment initializes persistent configuration on first run.
- Durable daily invocation budgets, queue limits, priority aging and source health.
- Bounded connector polling, poisoned-line quarantine, gap reporting and deduplication.
- Explicitly enabled package extensions and typed custom tool arguments.
- Offline backup, verified restore, and preview-first workspace retention.
- CI covers Python 3.11–3.13, installed wheels, Bubblewrap and container lifecycle.

## Upgrade notes

Back up runtime data before upgrading and install into a new environment. Stop
old services before switching their executable to the new environment.

Existing `latest_branch` metadata does not automatically become an accepted
base. Review the relevant completed proposal and explicitly accept its commit.
Omitted `write_paths` in JSON configuration now grants no write access. Default
protected paths include `check.py`, `tests/**` and `.github/**`; review your scopes
and checks when migrating. Shipped demo configurations now require validation.

A draft GitHub release contains the wheel, source archive, installer and SHA-256
checksums after validation. Publishing the draft is a maintainer action. Packages
are not automatically published to PyPI. OpenDots remains a local alpha; live
Codex behavior and stronger deployment boundaries require separate validation.
