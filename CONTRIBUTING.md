# Contributing

## Workflow

`main` is protected. Every change goes through a pull request; direct pushes are rejected.

1. Open or pick an issue. Each issue belongs to a phase milestone.
2. Create a branch from `main` named `<type>/<phase>-<short-desc>`, for example `feat/03-api-keys`.
3. Commit, push, and open a PR. Fill in the PR template and link the issue (`Closes #12`).
4. CI must pass: `lint`, `typecheck`, `test` and `pr-title`. Review conversations must be resolved.
5. PRs are squash-merged, so the PR title becomes the commit message on `main`. The branch is deleted after merge.

## PR titles

PR titles follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <lowercase subject without a trailing period>
```

Allowed types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`.

## Definition of done

- CI is green.
- New behaviour has tests. Bug fixes include a test that fails without the fix.
- No secrets in code. New settings are added to `.env.example`.
- Database changes include an Alembic migration.
- API changes include OpenAPI descriptions and examples.
- `CHANGELOG.md` is updated under `Unreleased`.

## Local setup

Added in Phase 02, together with the application skeleton.
