# Homelab production snapshot — 2026-10-22

This branch preserves the running homelab variant separately from upstream main.
The application reports `1.2.17`; upstream main currently reports `1.3.2`.
This is a production snapshot, not a claim that upstream features were merged.
The original upstream documentation remains available but may describe behavior
that differs from this branch.

## Included changes

- Upload preflight and post-conversion size checks, partial album reporting,
  preparation reuse and classified Telegram delivery errors.
- Media caching, pipeline timing, health endpoint, watchdog, resource metrics
  and privacy filtering for logs.
- Reddit JSON/HTML fallback, managed cookie readers and URL redirect checks.
- Instagram session handling and additional TikTok, Urlebird and Kick extractors.
- X posts without media return text, author and original link, with regression
  coverage for text, HTML escaping and existing media downloads.

Only application Python files, example message/configuration JSON, dependency
manifests, pytest configuration and tests were exported from production.
No live environment, cookies, browser profile, sessions, database, downloads,
logs, backup copies or homelab deployment credentials are included.
The production service was read only while preparing this snapshot.

## Running

Follow the base README to create a Python environment, install ffmpeg and
Playwright and configure the local Telegram Bot API. Copy the example JSON
files to their runtime names and configure credentials locally. Optional
cookie installation is a separate operational step; cookie readers do not
include a logged-in profile.

This repository does not include the private homelab Docker image, entrypoint,
NAS paths or the Rolês-SP HTTP extraction bridge. Deployments using those
components must retain their own configuration.

## Validation

See `VALIDATION.md` for the commands and verified scope of this snapshot.
