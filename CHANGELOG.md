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
