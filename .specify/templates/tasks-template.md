# Tasks: [FEATURE NAME]

**Feature**: [Link to spec.md, plan.md]
**Status**: Draft

## Format

`- [ ] T### [P?] [US#?] Description with file path`

- **T###** — sequential ID
- **[P]** — parallelizable (different files, no dependency on incomplete task)
- **[US#]** — belongs to user story (required in user story phases)
- File paths absolute when ambiguous, project-relative otherwise

## Phase 1 — Setup

- [ ] T001 ...

## Phase 2 — Foundational (blocking)

- [ ] T0NN ...

## Phase 3 — User Story 1 (P1)

**Goal**: ...
**Independent test**: ...

- [ ] T0NN [US1] ...

## Phase 4 — User Story 2 (P2)

**Goal**: ...
**Independent test**: ...

- [ ] T0NN [US2] ...

## Phase N — Polish

- [ ] T0NN ...

## Dependencies

```text
Phase 1 → Phase 2 → US1 → US2 → … → Polish
```

## Parallel Opportunities

Within Foundational: T### [P] + T### [P]
Within US1: T### [P] + T### [P]
…

## MVP Scope

User Story 1 only delivers a usable increment.
