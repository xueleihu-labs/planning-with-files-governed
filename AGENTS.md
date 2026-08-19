# AGENTS.md | planning-with-files-governed

This is the public community repository for the governed planning Skill.

## Scope

- Keep product code, public documentation, tests, schemas, and examples portable.
- Keep runtime state, checkpoints, receipts, caches, and machine-local planning data outside the release tree or under an explicitly ignored local-runtime directory.
- Never commit credentials, tokens, private absolute paths, user data, or host-specific configuration.

## Development

- Use the standard library and the project-local Python test configuration.
- Preserve backward-compatible plan and checkpoint contracts unless a migration is documented and tested.
- Prefer deterministic, fail-closed behavior for path resolution, attestations, locks, and handoffs.
- Run the focused regression tests first, then the complete test suite before a release commit.
- Do not push, publish, or alter external repositories from this working tree.

## Verification

- Check public-tree privacy scans.
- Check Python syntax and CLI help paths.
- Check Git diff scope and ignored runtime artifacts.
- Report any unavailable platform-specific validation explicitly.
