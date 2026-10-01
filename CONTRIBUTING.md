# Contributing to OpenDots

OpenDots is a local alpha. Keep contributions scoped to a concrete problem and
explain the resulting user behavior. Read [implementation](docs/IMPLEMENTATION.md)
and [remaining work](docs/ROADMAP.md) before extending runtime boundaries.

## Reproduce and develop

Use Python 3.11+, Git, and working Linux Bubblewrap namespaces. Create a virtual
environment, install with `python -m pip install -e .`, and run:

```bash
python -m unittest discover -s tests -v
```

When namespaces are unavailable, explicitly use trusted disposable fixtures:
`OPENDOTS_TEST_SANDBOX=trusted-local python -m unittest discover -s tests -v`.
The namespace CI job must still pass for changes to isolation behavior. Never
silently fall back to trusted-local in application code.

Reproduce a bug with a focused behavioral test. Preserve exact-action approvals,
owner-defined scopes and required checks, safe source/worktree separation and
interrupted-work inspection. Test migrations against an existing database when
changing persistence. Do not commit credentials, runtime databases or generated
upstream checkouts. Leave LICENSE unchanged unless the owner explicitly requests it.

## Pull requests

Explain the problem, change, validation and remaining limits. Prefer one concern
per PR. Local model mocks are useful for deterministic behavior tests, but do not
claim they prove live-model acceptance. CI covers supported Python versions,
installed packages, real namespaces and container lifecycle.

## Releases

Update both versions in `pyproject.toml` and `opendots/__init__.py`, describe
migration behavior in CHANGELOG.md, and merge only after CI passes. The draft
release workflow validates packages and attaches wheel, source archive, installer
and SHA-256 checksums. Review the draft before publishing it. Existing assets
are not overwritten. There is no automated PyPI publication.
