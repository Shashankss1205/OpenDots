# Validation record

Results distinguish current portable regression checks from historical real-agent acceptance. Repeated upstream runs are not additional unique OpenDots tests.

## Current version 0.1.5

| Check | Result | Boundary |
| --- | --- | --- |
| Python regression suite | 49 collected; 47 passed, 2 skipped | Explicit trusted-local fixtures; two namespace-only skips |
| Real process recovery | 8 assertions passed | Actual HTTP, Git, SQLite, workers, SIGKILL and restart; persisted owner plans, zero model calls |
| Rename/compatibility smoke | 11 assertions passed | Served logo, current/legacy headers, synthetic signed webhooks, module identity, legacy database discovery, PNG transparency |
| Clean wheel | Built, extracted and checked | Package bytes matched source; both command aliases/imports worked |

Recovery exercised externally stale work (failed), killed work (interrupted without replay), and a fresh task (completed). Final counts were one completed, one failed and one interrupted. Those process tests validate actual runtime behavior without live model dependence.

New structured Codex attempts during the follow-up timed out, including a 180-second attempt and smaller probes: **zero new successful model responses**. Chromium and working bubblewrap namespaces were unavailable on that host. Default isolation failed closed; portable fixtures explicitly selected trusted-local. Current results do not establish fresh browser layout, live-model or isolated-sandbox acceptance.

## Historical real-agent acceptance

September 30 acceptance used four owner goals, ten targets, five worker slots, 275 unique events, 822 HTTP deliveries, 548 duplicate deliveries and 116 actual Codex calls. It observed 56 completed repairs, 56 runtime checks and 56 independent reruns, with draft, rejection and deliberate stale-edit controls and 22 acceptance assertions. An earlier 100-call run exposed unchecked completion; `required_checks` was added before the successful fresh run.

| Upstream | Pinned revision | Full-suite result |
| --- | --- | --- |
| packaging | `f58537628042c7f29780b9d33f31597e7fc9d664269` | 48 passed |
| cachetools | `ca7508fd56103a1b6d6f17c8e93e36c60b44ca252` | 20 passed |
| click | `934813e4d421071a1b3db3973c02fe2721359a6e6` | 28 passed, 1 skipped, 1 xfailed |

Combined suites contained 27,796 tests per combined run; repetition produced 781,368 passing test invocations. These are repeated upstream executions, not unique OpenDots cases.

Earlier runs also exercised itsdangerous (297 passing) and blinker (25), React 19.1.1/ReactDOM with HappyDOM 18.0.1, public GitHub observation, and a disposable agentless K3s `v1.37.0+k3s1` API. Kubernetes validated replica readback and invalid-template rejection, not pod readiness. Historical desktop (1440×1120) and mobile (390×950) checks covered ten targets without overflow or JavaScript errors. Historical runtime checks used working bubblewrap.

Large raw reports, recordings and upstream clones were retained in the separately delivered release archive and omitted from this source tree. Publishing this repository does not imply those historical runs were repeated.

## Reproduce

```bash
python3 -m unittest discover -s tests -v
# Explicit trusted fixtures if namespaces are unavailable:
OPENDOTS_TEST_SANDBOX=trusted-local python3 -m unittest discover -s tests -v
python3 scripts/live_recovery.py --help
python3 scripts/live_scale.py --help
python3 scripts/live_case_set.py --help
```

Live runs require a responsive authenticated Codex CLI and runner-specific dependencies. See [DEVELOPMENT.md](DEVELOPMENT.md). No production security certification, multi-day soak, deployed-container execution, lossless ingestion or general crash-atomic side effects are claimed.
