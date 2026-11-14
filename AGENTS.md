# AGENTS.md

Entry point for non-Claude AI agents (Codex, Cursor, custom orchestrators) working on this repo.

This file mirrors [CLAUDE.md](CLAUDE.md) — pointers, not content. Read the docs/ graph for everything.

## What this project is

`scrapefold` — unified Python library for web scraping with one async API across 43 engines and a built-in anti-bot escalation ladder.

## Start here

1. [docs/README.md](docs/README.md) — index of all docs
2. [docs/conventions/golden-rules.md](docs/conventions/golden-rules.md) — the constraints
3. [CONTRIBUTING.md](CONTRIBUTING.md) — how to add an engine

## Run / test / lint

```bash
pip install -e ".[test]"
./scripts/check.sh                              # lint + type-check + offline tests
pytest -m "not paid and not network"            # default suite
```

## Conventions

- All code under `src/scrapefold/`. Tests under `tests/`. Docs under `docs/`. Scripts under `scripts/`.
- Async-everywhere. `httpx` for HTTP, `asyncio` for concurrency. No `requests` package.
- No vendor LLM SDK imports anywhere in `src/`. LLM is an injected callable.
- Engines are lazy-imported via `src/scrapefold/engines/__init__.py` registry.

## Task tracking

Plane project **SCRAPEFOLD** (`39ce4ed6-d9a8-4493-a7a2-8066268f3ac1`, tasks.aiagentlbs.com, workspace `sg`) is the source of truth for bugs, tech debt and follow-ups. `docs/TECH_DEBT.md` and `docs/post-1.0/backlog.md` mirror their Plane issue by `SCRAPEFOLD-<n>`.

States (owner decision 2026-11-01):

- **Todo**: accepted and not started. **In Progress**: code is being written.
- **In Review**: the code is finished and the PR is open or awaiting merge. Nothing is released yet.
- **Done**: shipped in a published release. Merged-but-unreleased work stays In Review.

Releases are Plane **Modules** named after the version (`v0.12.0`). Plane Releases returns 404 on this instance. When releasing (see CONTRIBUTING.md › Release):

1. Every CHANGELOG entry names its `SCRAPEFOLD-<n>`.
2. After the `vX.Y.Z` tag is pushed, verify that PyPI serves `X.Y.Z` and that the GitHub Release exists.
3. Create the `vX.Y.Z` module and add the shipped issues to it.
4. On each issue, comment with the version and the GitHub Release and PyPI links, then set it to **Done**.

An agent may perform step 4 only after the step-2 check passes. This is the repo exception to the shared rule that only a human sets Done.

## Output

Default CLI output is human-readable. Pass `--json` everywhere for machine-parseable output. Errors are fatal (non-zero exit + clear stderr message).

## Durable lessons and owner corrections

- Before finishing substantial work, check whether a recurring mistake, a verified workaround, or an owner correction yields a reusable rule.
- Check existing instructions and skills first. Update the matching rule in place, reconcile contradictions, and avoid duplicate or single-session skills.
- Keep project-specific guidance in this repository; put a reusable workflow in the existing relevant skill. Keep shared policies in AGENTS.md and make them available to Claude Code through CLAUDE.md.
- Turn a fixed behavioral bug into the smallest meaningful regression check, reusing the existing tests or evals. Instruction-only edits need a diff and consistency review, not an artificial test suite.
- Record verified guidance and when it applies. Keep secrets and private client material out of shared instructions and global skills; preserve existing access, approval, and data-storage boundaries.

## Shared instruction alignment

Read this AGENTS.md and the project guidance in [CLAUDE.md](CLAUDE.md) once before working. Shared policies apply to every agent; commands, models, and tools explicitly tied to one runtime apply only to that runtime. CLAUDE.md imports AGENTS.md so both agents receive the same shared rules. When changing a shared policy, update its existing source and reconcile the counterpart in the same change. Do not recursively reload these files.
