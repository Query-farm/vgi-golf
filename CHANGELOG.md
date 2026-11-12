# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.2.0]

- Port to VGI protocol 3.3 (vgi-python 0.51). Requires the current VGI DuckDB
  extension; 3.2 extensions are incompatible. No SQL surface changes: the
  `all_*` scan functions stay public.

## [0.1.0]

- Initial release for VGI protocol 3.2 (vgi-python 0.50) and vgi-lint-check 2.2.0.
- Current tables: `tours`, `current_leaderboard`, `current_schedule`,
  `current_player_stats` and `current_world_rankings`.
- Functions: `leaderboard()`, `schedule()`, `player_scorecard()`, `course_holes()`,
  `season_player_stats()`, `player()` and `world_rankings()` across the PGA Tour,
  DP World Tour, LIV Golf, LPGA Tour, PGA Tour Champions and Korn Ferry Tour.
- Honest User-Agent, bounded retries with `Retry-After`, redirect following, a
  bounded in-process response cache with politeness floors, and overridable base
  URLs for hermetic tests.
- Playoff handling: `schedule()` reports the regulation winning score (ESPN's
  season scoreboard folds playoff holes into many winners' scores) plus a
  `playoff` flag, and leaderboards carry `in_playoff`.
- Season statistics emit `NULL`, not ESPN's placeholder zeros, for golfers
  without measured rounds; OWGR "not ranked" history ranks (0) become `NULL`.
- Hermetic Haybarn end-to-end test over recorded fixtures, opt-in live tests, and
  agent-simulation tasks graded by reference SQL or `check_sql`.
