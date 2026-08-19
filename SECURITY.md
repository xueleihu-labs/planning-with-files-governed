# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in planning-with-files-governed, please report it responsibly.

- **Contact**: Use GitHub Private Vulnerability Reporting. Navigate to the repository's [Security page](https://github.com/xueleihu-labs/planning-with-files-governed/security) and select **Report a vulnerability** to submit a private report.
- **Response time**: Within 72 hours for initial acknowledgment.
- **Process**: We will investigate, develop a fix, and coordinate disclosure.

Please **do not** open a public GitHub issue for security vulnerabilities. Use the private reporting flow above instead.

When reporting, please include:
- A description of the vulnerability and its impact.
- Steps to reproduce (proof of concept if possible).
- Affected version(s).
- Suggested mitigation or fix (if any).

## Security Considerations

### Plan Files May Contain Sensitive Task Information

Plan files (`task_plan.md`, `1_master_plan.md`, `3_status_update.md`, `4_handoff.md`) may contain sensitive project information including architecture details, business logic, and task descriptions. Treat these files with appropriate access controls.

### Runtime State Must Not Be Committed to Git by Default

The following should be excluded from Git by default (add to `.gitignore`):

```
# Runtime state - do not commit
00.项目规划与治理/*/checkpoints/
00.项目规划与治理/*/evidence/
00.项目规划与治理/*/.write-lock
90.本机运行态/
```

Checkpoint files, evidence, and write locks are device-local runtime state. Committing them can leak device information and cause false conflict detection.

### External Checkpoint/Read-Head Data Is Untrusted Input

Checkpoint and handoff data received from external sources (other devices, other agents, file sync) must be treated as **untrusted input**. Before consuming any external checkpoint:

1. **Verify SHA-256** - confirm the checkpoint hash matches its recorded attestation.
2. **Validate schema** - ensure the checkpoint conforms to the expected JSON schema.
3. **Check predecessor chain** - verify the checkpoint correctly references its predecessor.
4. **Inspect content** - review the checkpoint content for unexpected or malicious data.

Never execute commands or apply file paths found in untrusted checkpoint data without validation.

### Schema Validation Does Not Guarantee Content Trustworthiness

JSON schema validation ensures structural correctness (required fields, correct types, valid values). It does **not** guarantee that the content is safe or trustworthy. A structurally valid checkpoint can still contain:

- Malicious file paths (see path traversal below).
- Misleading status information.
- Corrupted or tampered data that happens to pass schema validation.

Always combine schema validation with content review for untrusted data.

### File Paths Must Be Confined to Expected Root Directories

All file operations performed by planning-with-files-governed scripts must be confined to the project root and its planning subdirectories. No file should be read, written, or modified outside the expected root.

### Path Traversal Prevention

The system implements path traversal prevention by:

1. **Resolving all paths to absolute form** before any file operation.
2. **Checking that resolved paths start with the expected root** directory.
3. **Rejecting any path that escapes the root** via `..`, symlinks, or absolute paths.
4. **Normalizing path separators** (backslash to forward slash) before validation.

Example of a rejected path:
```
--project-root /path/to/project --plan-file ../../../etc/passwd
# REJECTED: resolved path escapes project root
```

### No Credentials in Plan Files

Plan files must **never** contain:
- Passwords, API keys, tokens, or secrets.
- Private keys, certificates, or `.env` file contents.
- Personally identifiable information (PII) beyond what is necessary for task planning.
- Database connection strings with credentials.

If a task requires credential references, use environment variable names or secret manager references - never inline values.

## Security Gates in CI

The CI pipeline includes:

- **Privacy scan**: checks for common secret patterns (API keys, tokens, private keys) in all files.
- **Path safety tests**: verifies that path traversal attempts are rejected.
- **Schema validation tests**: ensures all schemas are valid and enforce expected constraints.
- **Checkpoint integrity tests**: verifies SHA-256 chain validation works correctly.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.0.0-rc.3 | Pre-release under active evaluation; security reports accepted |
| 1.0.x      | Yes (stable supported line)                         |

The `2.0.0-rc.3` line is not a stable or long-term support commitment. Security reports affecting the RC may be submitted through GitHub Private Vulnerability Reporting; fixes and release timing are evaluated case by case.

## Disclosure Policy

- Security fixes are released as patch versions.
- Credits are given to reporters (unless they prefer to remain anonymous).
- A security advisory is published on GitHub after the fix is released.
