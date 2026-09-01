# Changelog

## v1.1.0 — 2026-09-01

- Added: manual update check from the About page using the public GitHub Releases API.
- Changed: Lightweight mode now uses a more compact unified drop/recent-operation surface.
- Fixed: the recent-operation presentation stays compact after Undo.

## v1.0.0 — 2026-08-31

首个 Windows standalone release。该版本冻结 feature behavior，并聚焦 packaging、
upgrade safety、documentation 和 launch validation。

- RJ lookup with normalized DLsite metadata and cover retrieval.
- Metadata cache and append-only observation/history storage.
- Current and historical confirmed translation relations with separate provenance.
- Folder scan and safe formatting of proposed names.
- Safe rename workflow with preview, explicit confirmation, preflight, durable
  journal, crash/recovery states, and Undo.
- Candidate relations using `same-maker-same-date v1`, kept non-confirmed and
  without score or probability.
- Local Manual Review and Review Queue history, including review-time evidence.
- Evaluation support for review history and derived datasets. The 24-pair Pilot
  documents workflow and taxonomy use; it is not an accuracy or precision evaluation.
- Bonus observation preservation from v0.10.3 onward, including the distinction
  between `NULL` unknown/legacy evidence and an explicitly empty bonus list.
- Transient bonus evidence preservation; no automatic recovery or detection of
  expired limited-bonus relations.

Candidate is derived/non-confirmed. The candidate policy remains
`same-maker-same-date v1`; it does not auto-confirm or learn from labels.
Automatic recovery/detection of expired limited-bonus relations is not implemented.

## 0.10.3 — 2026-08-30

- Preserve normalized bonus metadata observations in historical SQLite rows.
- Keep legacy `bonus_evidence_json = NULL` distinct from explicit `bonuses: []`.
- Do not infer or create bonus relations from transient observations.

## 0.10.2

- Add the local candidate Review Queue and expanded Manual Review taxonomy.
- Preserve candidate policy provenance and review-time evidence.

## 0.10.1

- Add the derived evaluation dataset workflow for manual review history.

## 0.10.0

- Add historical confirmed translation relation queries over metadata observations.

## 0.9.0

- Add explainable, non-scored relation candidates from local metadata.

## 0.5–0.8

- Add persistent metadata cache, append-only observations, and stale-cache
  fallback behavior.
- Harden provider parsing and explicit translation topology handling.
- Add safe Organizer preview, durable rename execution, crash recovery, and Undo.

## 0.1–0.4

- Bootstrap the desktop application, manual RJ lookup, DLsite metadata parsing,
  local folder scanning, naming, and the first safe rename preview flow.
