# Snapshot validation — 2026-10-22

## Source

Application Python modules, production regression tests, public example JSON,
dependency manifests and pytest configuration were read from the running
installation. Credentials and runtime data were excluded by an explicit export
allowlist. Public README and ignore rules were prepared locally.

## Initial comparison check

Command: `uv run --with-requirements requirements-dev.txt pytest -q` on Windows.
Result: **419 passed, 26 failed, 4 warnings**. This first check included upstream
regression files that were not present in the running variant. Those files
were subsequently removed when replacing the fork tree with the production
variant, rather than pretending they belonged to this implementation.

Known initial failure categories: obsolete upstream APIs, incomplete HTTP and
Playwright mocks, upload configuration mocks missing the newly used limit,
message example keys absent from the production templates, and a platform
specific resource-check expectation. These failures are not a successful full
validation. A live healthy bot does not establish regression-suite completeness.

## Final production-tree check

Command: `uv run --with-requirements requirements-dev.txt pytest -q --tb=short`.
Result on Windows: **419 passed, 12 failed, 4 warnings in 24.55 seconds**.
The public upstream fixtures needed by the production tests are retained.

The 12 unresolved failures are:
- Threads/Reddit/Kwai redirect test doubles missing response attributes (3).
- Reddit extraction-failure message key missing from example JSON (2).
- Watchdog test mocking `/proc/meminfo` while runtime reads cgroups/psutil (1).
- Reddit JSON media/crosspost expectations (3).
- Album upload test mocks missing `TELEGRAM_MAX_UPLOAD_MB` (2).
- TikTok Playwright mock missing the `route` interface (1).

These results are recorded without disabling tests or changing live source to
force a green suite. The snapshot is suitable for preserving the running
variant, but it is not claimed to be regression-clean or a release candidate.
No real external download or Telegram delivery was initiated for publication.

## Security scope

No environment files, cookies, session files, databases, browser profiles,
logs, media, private Compose files or backups are published. Pattern scanning
found only a dummy Telegram token already present in the public env examples.
`git diff --check` was run. No production files were changed by this export.
