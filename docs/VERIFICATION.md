# Verification snapshot — 2026-10-07

This file records local evidence from the implementation pass. It is not a
claim about Linux packet capture, Docker, GPU inference, GCP capacity, or
scientific results.

| Gate | Result |
|---|---|
| `uv lock --check` | passed; 104 packages resolved |
| Ruff lint / format | passed; 111 Python files formatted |
| Pyright strict | passed; 0 errors, 0 warnings |
| Import Linter | passed; 7/7 architecture contracts kept |
| Python tests | passed; 179 tests, warnings treated as errors |
| Python coverage | 83% overall; 178-test coverage run passed the 80% floor |
| Focused post-upgrade crypto tests | passed; 18 tests |
| `pip-audit` | no known vulnerabilities after `cryptography` 50.0.2 upgrade |
| `pnpm audit --prod` | no known vulnerabilities (official npm registry) |
| Console `pnpm check` | lint, typecheck, 4 tests, and Vite production build passed after migration (2026-10-11) |
| Alembic offline upgrade | migrations 0001 and 0002 generated successfully |
| Terraform | provider init, recursive format check, and validate passed |
| GCE startup scripts | Bash syntax check passed |
| GitHub workflows | all workflow YAML parsed successfully |

Docker Engine was unavailable and was not retried. The container and live
service checks still required are maintained in
[`DOCKER_BLOCKED_VALIDATION.md`](DOCKER_BLOCKED_VALIDATION.md).
