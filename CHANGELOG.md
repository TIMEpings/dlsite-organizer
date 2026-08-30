# Changelog

## Unreleased — v1.0.0 release preparation

This release preparation freezes feature behavior and focuses on packaging,
upgrade safety, documentation, and launch validation.

- Safe rename workflow with preview, explicit confirmation, preflight, durable
  journal, crash/recovery states, and Undo.
- Current and historical DLsite-confirmed translation relations with separate
  provenance.
- Append-only metadata observation and translation history.
- Candidate relations using `same-maker-same-date v1`, kept non-confirmed and
  without score or probability.
- Local Manual Review and Review Queue history, including review-time evidence.
- Bonus observation preservation from v0.10.3 onward, including the distinction
  between `NULL` unknown/legacy evidence and an explicitly empty bonus list.
- No automatic recovery or detection of expired limited-bonus relations.

The public v1.0.0 tag remains gated on a user-selected `LICENSE`, final Windows
manual smoke, and final artifact verification.

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
