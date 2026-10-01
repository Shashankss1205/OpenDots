# Implementation details

This document describes the code shipped in OpenDots 0.1.5. OpenDots is an independent local prototype inspired by the Dots idea. Its persistent unit is a **target** with an objective, workspace, subscriptions, desired state, and owner policy. Goal groupings used in acceptance runners are metadata, rather than a separate autonomous goal planner.

## Source map

| Path | Responsibility |
| --- | --- |
| `opendots/config.py` | Immutable configuration, target validation, path resolution |
| `opendots/engine.py` | Ingestion, routing, attention, scheduling, planning, execution and completion gates |
| `opendots/store.py` | SQLite transactions, task state, approval cursors, audit, memory and recovery |
| `opendots/agents.py` | Agent registry, deterministic planner and structured Codex CLI planning |
| `opendots/tools.py` | Tool registry, argument validation, file tools and bounded checks |
| `opendots/workspaces.py` | Source snapshots, task worktrees, commits, branches and patches |
| `opendots/evidence.py` | Workspace fingerprints and check-configuration signatures |
| `opendots/process_guard.py` | Linux worker-death supervision and command-group cleanup |
| `opendots/sandbox.py` | Bubblewrap isolation, mounts, environment and resource limits |
| `opendots/sources.py` | Source registry, GitHub polling, JSONL cursors and timer events |
| `opendots/server.py` | Local HTTP API, webhook authentication and static dashboard |
| `opendots/__main__.py` | CLI commands and error reporting |
| `opendots/__init__.py` | Version metadata |
| `opendots/web/` | HTML, JavaScript, CSS and served logo |
| `spots/` | Compatibility imports and command delegation |
| `tests/` | Runtime, extension and completion-evidence tests |
| `examples/` | Starting config, event fixtures, case sets and small workspaces |
| `scripts/` | Deterministic demos, live acceptance and recovery runners |
| `assets/` | Transparent OpenDots symbol and wordmark |
| `Dockerfile`, `compose.yaml`, `deploy/` | Optional deployment templates |

## Configuration and target ownership

The CLI defaults to `examples/config.json`. Paths resolve relative to that file. Configuration defaults include four workers, the deterministic `demo` backend, a 180-second agent timeout, eight planning rounds, `codex` as the CLI executable, and `bubblewrap` for checks. The default database is `../.opendots/state.db`; if this default is absent and the legacy `../.spots/state.db` exists, the legacy database is reused. Explicit nondefault database paths are unchanged.

Each target has an ID, name, objective, source workspace, subscriptions, policy, named checks, minimum attention priority, desired state, skills, optional agent override, write scopes, and optional `required_checks`. Target IDs must be unique. Source workspaces must not overlap. Use separate source worktrees for independent targets concerning the same upstream project.

A tool policy is `auto`, `ask`/`approval`, `draft`, or `deny`. Unspecified tools are denied. The default write scope is `*`; owners can narrow it using `write_paths`. Required-check names must refer to configured checks. Checks are argv arrays executed without a shell; `{python}` selects the interpreter. The owner controls executable commands and trusted skills.

## Event ingestion, attention and routing

The normalized envelope contains `id`, `type`, `source`, `payload`, and optional `target_id` and `priority`. JSON input is bounded to 256,000 bytes; event IDs and types are bounded to 256 characters. Subscriptions match event types/globs with source and repository filters. Explicit targeting still follows the engine's validation and routing rules.

Delivery IDs are durable. Repeating the same ID with the same canonical body returns a duplicate result; reusing it with a different body is rejected. Each event/target work pair is unique. Audit records also capture events that cause no work.

Attention priorities are critical 100, high 80, normal 50 and low 10; star observations normally receive 5. Explicit priority is an integer from 0 to 100. The target's default minimum is 20. Cheap reducers update observed state, such as stars or issues, without invoking a planner when an event is below the attention threshold.

## Scheduler and persistent state

A process lock prevents multiple schedulers from owning the same database. A thread pool runs independent targets concurrently. A SQLite partial unique index restricts a target to one active task in `running`, `waiting_approval`, or `ready`. Approval blocks subsequent work for that target. Scheduling considers ready resumptions first, then queued priority descending and ID order; running tasks are not preempted.

A task moves from `queued` to `running`. An approval action persists its plan/cursor and enters `waiting_approval`; approval makes it `ready` for resumption. Rejection and execution terminate in the appropriate recorded outcome. Terminal statuses include `completed`, `drafted`, `blocked`, `failed`, and `interrupted`. Startup marks uncertain running work interrupted rather than automatically replaying side effects.

SQLite uses WAL, foreign keys, transactions and a 15-second connection timeout. Tables include `targets`, `events`, `work`, `audit`, `schedule_ticks`, and `source_state`. The database stores observed/desired state, plans, action results, approval tokens, cursors, source positions and memory. Filesystem operations and SQLite commits do not form one atomic cross-system transaction.

## Planning and policies

An agent implements `plan(target, event, state)`. Plans contain a summary, actions and an outcome: `complete`, `needs_follow_up`, or `blocked`. The legacy deterministic format can omit the outcome and is treated as complete. A follow-up round receives executed action results; the configured round budget bounds investigation.

The Codex provider launches the actual CLI with `-a never exec --ephemeral --sandbox read-only --skip-git-repo-check --cd ... --output-schema ... --output-last-message ... -`, plus an optional model. The registry supplies the JSON schema. Context includes bounded workspace snapshots, owner objective/skills, desired and observed state, memory, the event, current round and prior results. Snapshot budgets are 128,000 bytes total and 32,000 per file; skills are bounded to 64,000 bytes, summaries to 4,000 characters and results to 1 MB. Planner errors do not fall back to the demo provider.

Plans propose application tools; the engine independently validates arguments, policy and scopes. `auto` executes. `ask`/`approval` persists a preview and pauses. `draft` retains a proposal without executing that action or later actions; earlier automatically executed actions may already have occurred. `deny` blocks. Approval tokens bind the task ID, planning round, action index and exact arguments with SHA-256. HTTP approval must supply the current token. CLI `decide` selects the current pending action directly.

The planner's own environment, CLI hooks and plugins remain a separate boundary. Application policy does not attest every action of an externally configured planner.

## Built-in tools and file safety

| Tool | Behavior |
| --- | --- |
| `read_file` | Reads bounded text and returns a content hash |
| `write_file` | Requires the expected hash or `absent`, applies write scopes, atomically replaces content |
| `replace_text` | Requires the whole-file hash and exactly one occurrence of the old text |
| `run_check` | Executes a named owner-configured argv with bounded time and output |
| `note` | Records a bounded memory proposal |

File content is bounded to 256,000 bytes; notes to 8,000 characters. File mutation writes an fsynced temporary file and uses atomic replacement, preserving mode where applicable. Absolute paths, traversal, escaping links and protected paths such as `.git`, `.aws`, `.ssh`, `.codex` and `.env` are rejected. Owner write globs add another constraint. Check execution has a 30-second default bound and captures at most 65,536 bytes of output.

## Git proposal workspaces

The workspace manager snapshots source content into managed Git repositories, excluding credential paths, `.git`, Python caches and dependency folders such as `node_modules`. Git hooks/configuration are disabled for managed operations. Each task gets a worktree and a branch in the `opendots/<namespace>/task...` namespace. The namespace distinguishes database/target ownership.

A target's next task starts from its last explicitly accepted proposal, or the initial snapshot when none has been accepted. Completion retains a local commit, branch and patch; the source workspace stays separate. Inspect `opendots proposal ID` and the retained patch, then use `opendots accept ID --commit COMMIT`. The terminal provides `/proposal ID` and `/accept ID COMMIT`. Acceptance binds the exact completed commit, rejects dirty worktrees and stale bases, and waits for active work to finish or be cancelled. Existing `latest_branch` metadata is informational and is not automatically accepted on upgrade. External edits to the original source are not automatically imported after the initial snapshot. There is no built-in source push or PR publication.

## Completion evidence and shared memory

`required_checks` closes the unchecked-completion path. If a complete plan omits a required check, the engine appends an audited owner-required action through normal policy and approval handling. The original model plan remains recorded. Persisted plans follow the same rule without resetting action cursors.

Passing evidence is task-local and bound to both a workspace fingerprint and a check signature containing argv/sandbox settings. The fingerprint covers content, modes and relevant links, including Git-ignored inputs. Protected credential locations and standard generated caches are excluded. Checks must see stable inputs before and after execution. File edits invalidate previous evidence, external changes expire it, and legacy unsigned evidence must be revalidated. Earlier tasks' successful checks cannot satisfy a new task.

The engine checks evidence around artifact retention before completion. Notes enter shared target memory only in successful completion transactions; failed or interrupted notes stay in the audit trail. Recent memory retains up to 50 notes/artifacts. This is a content-based local check gate, not full dependency attestation: excluded inputs, external services and executable/runtime changes outside the signed configuration remain limitations.

## Process lifetime and check isolation

On Linux, bounded commands run through an isolated Python supervisor, without `preexec_fn`. The supervisor uses `prctl` parent-death signaling with a race check; the actual command owns a separate process group. Timeout, worker death and normal root-command exit clean remaining ordinary descendants and preserve the root exit status. Deliberately detached sessions are outside this guarantee.

Bubblewrap is the default and fails closed when namespaces are unavailable. It unshares namespaces, disables network access, clears the environment, mounts runtime inputs read-only and the task workspace writable, supplies private temporary/device mounts, and dies with its parent. If `prlimit` is available, limits include 20 CPU seconds, 1 GiB address space, 16 MiB file size and 128 open descriptors. Explicit `trusted-local` skips process/network isolation; it is intended for owner-controlled fixtures, and is never selected automatically.

## Sources and timers

- **GitHub polling:** reads the latest events page, up to 100 records; uses ETags and server polling intervals and persists state. `bootstrap: observe` records existing history without replay; `replay` ingests the recent response. High-volume gaps can be missed, so polling is not lossless.
- **JSONL:** persists a byte cursor, processes complete lines, retries an incomplete final line and resets on truncation. Stable upstream IDs support deduplication.
- **Timers:** durable IDs bind schedule ID and interval tick. Startup emits the current interval; missed intervals collapse to the current tick rather than backfilling every missed occurrence.
- **GitHub webhooks:** verify HMAC-SHA256 using `OPENDOTS_GITHUB_WEBHOOK_SECRET`, with the legacy secret name as a compatibility fallback. Delivery/event headers are normalized into the same event stream.

## HTTP API and dashboard

| Route | Purpose |
| --- | --- |
| `GET /api/health` | Health |
| `GET /api/state` | Targets, recent work/audit and all-time counts |
| `GET /api/work/{id}/patch` | Completed retained patch, at most 1 MiB |
| `POST /api/events` | Normalized event ingestion |
| `POST /api/work/{id}/decision` | Token-bound approval/rejection |
| `POST /api/webhooks/github` | Signed webhook delivery |

The threaded HTTP server defaults to loopback. Local mutation routes require JSON content type and `X-OpenDots-Request: dashboard`; the legacy header is accepted with the same guard. Host and supplied Origin must match allowed local values. Signed webhooks use signature authentication instead. Responses include no-store, nosniff and a content-security policy. Patch delivery rejects escaping/symlink paths.

The dashboard polls each second, displays backend labels, target cards, recent workflow, queue and audit, and renders untrusted text using `textContent`. Review disclosures, focus and scroll are maintained across refreshes. State presents bounded recent records alongside all-time counts. The interface is a local control surface, not a multi-user authentication system.

## Extensions, packaging and compatibility

`AgentRegistry.register` replaces/adds planners. `ToolRegistry.register(name, handler, arg_names)` adds named-string argument tools; custom handlers must implement their own scope and preview rules and remain denied until owner policy grants them. `SourceRegistry.register(kind, factory)` adds adapters that emit normalized envelopes with persisted source state. There is no fixed architectural target/tool/provider count.

Python 3.11+ and the standard library are sufficient for the application; setuptools 68+ builds the wheel. Git and bubblewrap are external executables. Package data includes dashboard assets. Both `opendots` and `spots` console scripts invoke the canonical CLI. Legacy module aliases resolve to the canonical module objects, including process supervision.

Docker/Compose and systemd templates provide optional starting points. Compose binds the host port to loopback and persists `.opendots`; no Codex installation or credentials are bundled. Their deployment execution is not part of the current validated results.

## Scope boundaries

Implemented tools handle local files, checks and notes. There are no built-in authenticated outbound GitHub/Slack/email actions, arbitrary browser/MCP execution or automatic PR publication. The GitHub connector used to publish this repository is a development workflow, not an OpenDots runtime feature. Production hosting, multi-day soak behavior, lossless ingestion, arbitrary side-effect atomicity and automatic upstream synchronization are not claimed. See [validation](VALIDATION.md) for evidence and prerequisites.
