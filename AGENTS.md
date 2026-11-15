# AGENTS.md — Resume_Assistant Engineering Instructions

This is the permanent instruction document for every coding agent working on
this repository (OpenCode, Google Antigravity, and any other agent or
framework). Read it before starting any mission. Keep it updated only through
explicit, reviewed changes.

---

## A. Mandatory Project Onboarding

Before starting any mission, every agent must:

1. Read `AGENTS.md` (this file).
2. Read `docs/PROJECT_STATUS.md`.
3. Inspect the actual project structure.
4. Inspect the current Git status.
5. Inspect the existing implementation relevant to the assigned task.
6. Understand completed work, unresolved issues, and the current stopping
   point.

Never assume that a previous agent's report accurately describes the current
implementation. Engineering reports are claims, not facts — verify against
source code. The progress file (`docs/PROJECT_STATUS.md`) is a navigation
aid, not a substitute for inspecting source code.

---

## B. Development Environment

- **Python 3.12** (`requires-python = ">=3.12"`; verified with 3.12.14).
- **Always use the project `.venv`.** Never install project dependencies into
  system Python.
- Run tests through `.venv/bin/python -m pytest`.
- Run dependency checks through `.venv/bin/python -m pip check`.
- Respect `pyproject.toml` for dependencies and tool configuration.
- Do not introduce unnecessary dependencies.

---

## C. Privacy and Security

The following must **never** be committed:

- `data/private/` (Master Resume, processed candidate evidence JSON, any
  private application records).
- `.env` and other environment files containing secrets.
- `.venv/`, `venv/`, and other virtual environments.
- Generated personal resumes and generated candidate evidence JSON.
- Credentials and API keys.

Never print or expose secret values (including `.env` contents or API keys).

Do not send personal resume information to external services unless
explicitly authorized for the assigned mission.

---

## D. Engineering Standards

- Prefer deterministic Python implementations over unnecessary LLM calls.
- Use Pydantic for structured application data where appropriate.
- Write meaningful tests.
- Test failure paths, not just successful execution.
- Preserve source provenance when processing candidate information
  (source filename, SHA-256, and source location on every evidence record).
- Avoid unnecessary agent frameworks, RAG, vector databases, and
  abstractions.
- Keep modules focused and maintainable.
- Preserve existing behavior unless a change is explicitly required.

---

## E. Mission Management

Every coding task must have a defined scope and acceptance criteria.

Agents must:

- Work only within the assigned scope.
- Avoid implementing unrelated future features.
- Preserve existing work (never reset, clean, overwrite, or discard existing
  modifications).
- Run relevant regression tests.
- Document known limitations.
- Update `docs/PROJECT_STATUS.md` after completing their assigned work.

Never mark a mission as human-approved merely because implementation and
tests passed.

Use these statuses:

- `NOT STARTED`
- `IN PROGRESS`
- `IMPLEMENTED — AWAITING REVIEW`
- `APPROVED`
- `BLOCKED`

Only an explicit human approval can change a mission to `APPROVED`.

---

## F. Git Discipline

- Inspect changes before committing (`git status`, `git diff --cached`,
  `git diff --cached --name-only`).
- Never use force push.
- Never rewrite shared history.
- Never commit `.env`, private data, or generated candidate documents.
- Never run destructive Git commands without explicit authorization.
- Do not commit or push automatically after completing an implementation
  mission — stop at the human approval gate.
- Use meaningful commit messages when commits are authorized.
