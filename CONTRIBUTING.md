# Contributing to planning-with-files-governed

Thank you for your interest in contributing! This document outlines the process for contributing to the project.

---

## Getting Started

### Prerequisites

- Python ≥ 3.9
- Git
- No external dependencies required

### Fork and Clone

1. Fork the repository on GitHub.
2. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/planning-with-files-governed.git
cd planning-with-files-governed
```

3. Add the upstream remote:

```bash
git remote add upstream https://github.com/xueleihu-labs/planning-with-files-governed.git
```

---

## Development Workflow

### 1. Create a Branch

```bash
git checkout main
git pull upstream main
git checkout -b feature/your-feature-name
```

Use descriptive branch names:
- `feature/add-new-check` - for new features
- `fix/checkpoint-validation` - for bug fixes
- `docs/update-readme` - for documentation changes
- `test/improve-coverage` - for test improvements

### 2. Make Your Changes

- Follow the existing code style and conventions.
- Keep changes focused and minimal.
- Add or update tests for any functional changes.
- Update documentation if your change affects user-facing behavior.

### 3. Test Your Changes

```bash
# Compile check
python3 -m py_compile scripts/*.py

# Run tests
python3 -m pytest tests/ -q

# Validate schemas
python3 scripts/validate-schemas.py

# Run plan doctor on example projects
python3 scripts/plan-doctor.py --project-root examples/basic --task-id example-task
```

All tests must pass before submitting a PR.

### 4. Commit Your Changes

Use clear, conventional commit messages:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat` - new feature
- `fix` - bug fix
- `docs` - documentation only
- `test` - test only
- `refactor` - code refactoring
- `chore` - maintenance tasks
- `security` - security-related changes

**Examples:**
```
feat(checkpoint): add SHA-256 chain verification

fix(plan-doctor): handle missing task-index.yaml gracefully

docs(readme): update governance levels table

test(handoff): add cross-platform handoff test cases
```

### 5. Push and Create a Pull Request

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub with:
- A clear title following the commit message format.
- A description of what changed and why.
- Reference to any related issues.
- Confirmation that tests pass.

---

## Code Style

### Python

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Use 4-space indentation.
- Maximum line length: 100 characters.
- Use descriptive variable and function names.
- Add docstrings to all public functions and classes.
- Type hints are encouraged but not required.

### Markdown

- Use ATX-style headers (`#`, `##`, `###`).
- Use fenced code blocks with language hints.
- Keep line length reasonable (wrap at ~100 characters for prose).
- Use tables for structured data.

### File Organization

- Scripts go in `scripts/`.
- Tests go in `tests/`.
- Schemas go in `schemas/`.
- Templates go in `templates/`.
- Documentation goes in `docs/` or the root directory.
- Examples go in `examples/`.

---

## Pull Request Review

All PRs are reviewed for:

1. **Correctness** - does the change do what it claims?
2. **Tests** - are there adequate tests?
3. **Security** - does the change introduce security risks?
4. **Compatibility** - does the change break existing functionality?
5. **Documentation** - is documentation updated if needed?
6. **Code style** - does the change follow the style guide?

### Review Timeline

- Initial review: within 3-5 business days.
- Follow-up reviews: within 2-3 business days.

---

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests.
- Use the provided issue templates (`.github/ISSUE_TEMPLATE/`).
- Search existing issues before creating a new one.
- Include as much detail as possible (OS, Python version, steps to reproduce).

---

## Security Vulnerabilities

Do **not** report security vulnerabilities via GitHub Issues. See [SECURITY.md](SECURITY.md) for the vulnerability reporting process.

---

## Code of Conduct

Be respectful and constructive in all interactions. Harassment, discrimination, and toxic behavior are not tolerated.

---

## License

By contributing, you agree that your contributions are licensed under the MIT License, under the dual copyright:

```
Copyright (c) 2026 Ahmad Adi
Copyright (c) 2026 xueleihu52-arch
```

