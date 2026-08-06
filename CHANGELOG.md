# Changelog

All notable changes to planning-with-files-governed are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.0.0 - 2026-08-06

### Added
- Community Edition release based on production v1.2.0
- L0-L3 governance profiles (LIGHT_FAST, LIGHT_CONTROLLED, STANDARD, STRICT)
- Layout v3 with project-local task planning
- Checkpoint consumption and recovery
- SHA-256 plan attestation
- Smart hook injection (userprompt, pretool, precompact)
- Session catchup for recovery
- Plan doctor diagnostics
- Cross-platform support (macOS, Windows, WSL)
- 8 JSON schemas for workflow contracts
- Comprehensive test suite

### Changed
- Renamed LOBSTER_ROOT to PWF_ROOT
- Renamed fuxi_read_head to external_read_head (with backward-compatible read)
- Renamed fuxi_risk_route to governance_route
- Genericized all paths and terminology
- Removed personal rule profile system

### Removed
- Personal rule profiles (lobster-work)
- Internal acceptance reports
- Internal references and terminology
