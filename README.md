<p align="center">
  <a href="https://query.farm/vgi/">
    <img src="https://raw.githubusercontent.com/Query-farm/vgi-kalshi/main/docs/vgi-logo.png" alt="Vector Gateway Interface logo" width="320">
  </a>
</p>

<h1 align="center">vgi-golf</h1>

<p align="center">
  Professional golf leaderboards, schedules, scorecards, season statistics and world rankings as ordinary DuckDB tables.<br>
  A <strong>read-only</strong> <a href="https://query.farm/vgi/">VGI</a> worker, built by <a href="https://query.farm">🚜 Query.Farm</a>
</p>

<p align="center">
  <a href="https://github.com/Query-farm/vgi-golf/actions/workflows/ci.yml"><img src="https://github.com/Query-farm/vgi-golf/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.13%2B-3776AB.svg" alt="Python 3.13+">
  <a href="https://query.farm/vgi/"><img src="https://img.shields.io/badge/VGI-read--only-6f42c1.svg" alt="Read-only VGI worker"></a>
</p>

---

`vgi-golf` fetches golf data on demand and reformats the responses as typed
Arrow data for Haybarn SQL. It covers six tours: the PGA Tour (majors
included), DP World Tour, LIV Golf, LPGA Tour, PGA Tour Champions and Korn
Ferry Tour. It also covers the men's Official World Golf Ranking. It needs no
credentials, and it does not rehost or store any dataset.

## Quick start

The worker is not published to PyPI. `uvx` runs the tagged release straight from
GitHub and caches it after the first use:

```bash
uvx --from git+https://github.com/Query-farm/vgi-golf@v0.2.0 vgi-golf --help
```

Launch Haybarn with the catalog pre-attached:

```bash
uvx haybarn-cli \
  -cmd "INSTALL vgi FROM community; LOAD vgi; ATTACH 'golf' (TYPE vgi, LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-golf@v0.2.0 vgi-golf');" \
  :memory:
```

Or attach it in an existing Haybarn session:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'golf' (
  TYPE vgi,
  LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-golf@v0.2.0 vgi-golf'
);
```

For source development, run `uv lock --check`, then `uv sync` and
`uv run vgi-golf` (or `uv run golf_worker.py`). The lockfile resolves every
dependency from package registries.

## Catalog surface

The catalog has five tables and seven parameterized table functions. Every
`tour` argument accepts `pga`, `eur`, `liv`, `lpga`, `champions-tour` or `ntw`,
and defaults to `pga`.

| Kind | Objects |
| --- | --- |
| Current tables | `tours`, `current_leaderboard`, `current_schedule`, `current_player_stats`, `current_world_rankings` |
| Tournaments | `schedule(tour, season)`, `leaderboard(tour, event_id)`, `course_holes(event_id, tour)` |
| Scorecards | `player_scorecard(event_id, player_id, tour)`, with hole-by-hole scores |
| Players | `season_player_stats(season)` (PGA Tour), `player(player_id)` |
| Rankings | `world_rankings(max_rank)` (OWGR) |

Tournaments use ESPN's `event_id` and golfers use ESPN's `player_id`. A golfer
keeps the same ESPN id on every tour. The world ranking uses OWGR's own
`owgr_player_id`, so join it to the ESPN tables by name.

## Usage

### This week's tournament

`current_leaderboard` shows the PGA Tour event that is in progress. If the
next event has not started, it shows the most recently finished event.
Canceled events are skipped.

```sql
SELECT event_name, position, player_name, score_to_par, thru, status
FROM golf.main.current_leaderboard
ORDER BY sort_order
LIMIT 10;
```

`leaderboard()` covers any tour and any past event. Leave out `event_id` to get
that tour's current event:

```sql
-- The final 2026 Masters
SELECT position, player_name, score_to_par,
       round_1, round_2, round_3, round_4, earnings
FROM golf.main.leaderboard(event_id := 401811941)
ORDER BY sort_order;

-- This week on the DP World Tour
SELECT position, player_name, score_to_par
FROM golf.main.leaderboard(tour := 'eur')
ORDER BY sort_order
LIMIT 10;
```

`score_to_par` is an integer, with even par (E) stored as 0. `position` is the
text ESPN displays, such as `T3`, and `position_number` holds the rank as a
number. Players who missed the cut keep their first two rounds and have
`status = 'Missed Cut'`. Team events, such as the Zurich Classic, have
`entry_type = 'team'` rows with no `player_id`.

### Schedules and winners

```sql
SELECT start_date, event_name, winner_name, winning_score
FROM golf.main.schedule(season := 2025)
ORDER BY start_date;

SELECT winner_name, count(*) AS wins
FROM golf.main.current_schedule
WHERE completed
GROUP BY winner_name
ORDER BY wins DESC;
```

`winning_score_to_par` is `NULL` for cup and match-play events, such as the
Ryder Cup. For those events, `winning_score` holds the points.

`playoff` marks events decided in a playoff. ESPN's season scoreboard folds
playoff holes into some winners' scores, so for playoff events the worker
rebuilds `winning_score` from the regulation rounds: the 2026 World Wide
Technology Championship shows Blades Brown at -23, not ESPN's -25. On a
leaderboard, `in_playoff` marks the players who went to extra holes, and their
`current_round` and `thru` describe the playoff.

### Hole-by-hole scorecards and course setup

```sql
SELECT round, hole, par, strokes, score_type
FROM golf.main.player_scorecard(401811941, 9478)
ORDER BY round, hole;

-- Rory McIlroy's 2026 scoring on Augusta's par 3s
SELECT c.hole, c.yards, avg(s.score_to_par) AS avg_to_par
FROM golf.main.course_holes(401811941) c
JOIN golf.main.player_scorecard(401811941, 3470) s USING (hole)
WHERE c.par = 3
GROUP BY ALL
ORDER BY c.hole;
```

Some tours, notably LIV Golf, publish only round totals. For those events,
`player_scorecard()` returns no rows, and `leaderboard()` still has the round
scores.

### Season statistics and the FedExCup

ESPN publishes season statistics only for the PGA Tour:

```sql
SELECT fedex_cup_rank, player_name, fedex_cup_points, wins, earnings, scoring_average
FROM golf.main.current_player_stats
ORDER BY fedex_cup_rank
LIMIT 10;

SELECT player_name, driving_distance, driving_accuracy_pct, greens_in_regulation_pct
FROM golf.main.season_player_stats(season := 2025)
WHERE rounds_played >= 40
ORDER BY driving_distance DESC
LIMIT 10;

-- 2025 major champions with their season wins and FedExCup points rank
SELECT s.event_name, s.winner_name, s.winning_score,
       p.wins AS season_wins, p.fedex_cup_rank
FROM golf.main.schedule(season := 2025) s
JOIN golf.main.season_player_stats(season := 2025) p ON p.player_id = s.winner_id
WHERE s.event_name IN ('Masters Tournament', 'PGA Championship', 'U.S. Open', 'The Open')
ORDER BY s.start_date;
```

Percentages use a 0-100 scale. Money is `DECIMAL(14,2)` US dollars. Golfers
ESPN does not measure (mostly non-members) have `NULL` rounds and per-round
averages rather than ESPN's placeholder zeros. `fedex_cup_rank` orders the
season's FedExCup points; once the TOUR Championship is played it is not the
final FedExCup finish, which starting strokes decide (Scottie Scheffler led the
2025 points while Tommy Fleetwood won the FedExCup).

### Players and the world ranking

```sql
SELECT player_name, birth_date, birth_city, college, turned_pro
FROM golf.main.player(9478);

SELECT rank, player_name, country, average_points, events_played
FROM golf.main.current_world_rankings
ORDER BY rank
LIMIT 10;

-- Read deeper than the table's top 1,000
SELECT count(*) AS japanese_golfers
FROM golf.main.world_rankings(max_rank := 2000)
WHERE country_code = 'JPN';
```

## Data sources, licence and affiliation

`vgi-golf` is an independent project. It is **not affiliated with, endorsed
by or sponsored by** ESPN, The Walt Disney Company, the PGA TOUR, the DP World
Tour, LIV Golf, the LPGA, the Korn Ferry Tour, PGA Tour Champions, Augusta
National or the Official World Golf Ranking. All names are used only to
identify the data. The worker contains no logos, and it does not return the
upstream logo or headshot URLs.

- **ESPN** provides leaderboards, schedules, scorecards, season statistics and
  profiles through the `site.api.espn.com`, `site.web.api.espn.com` and
  `sports.core.api.espn.com` hosts. These endpoints are **undocumented and
  unofficial**. They may change or disappear without notice. Their use is
  subject to the [Disney/ESPN Terms of Use](https://disneytermsofuse.com/),
  which restrict automated access and commercial reuse.
- The **Official World Golf Ranking** comes from the public backend of
  owgr.com (`apiweb.owgr.com`). That backend is undocumented and carries no
  published data licence. The ranking data belongs to OWGR.

The worker fetches data only when a query asks for it. It never rehosts,
redistributes or bulk-archives the data. It caches responses briefly in
memory, identifies itself with an honest User-Agent
(`vgi-golf/<version> (+https://github.com/Query-farm/vgi-golf)`), and limits
how many requests it sends at once. The data belongs to its owners. **You are
responsible for making sure that your use, and especially any commercial use
or redistribution, complies with the upstream terms.** The MIT licence below
covers only this worker's source code, not the data it retrieves.

## Production HTTP deployment

Stdio is the recommended transport when Haybarn can launch the worker locally.
Use HTTP when several hosts need to share the worker. The production HTTP
entry point refuses to start unless it has a stable signing key and either
authentication or an explicit decision to allow anonymous access:

```bash
export VGI_SIGNING_KEY='a-long-random-secret-shared-by-every-replica'
export VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn'

uvx --from git+https://github.com/Query-farm/vgi-golf@v0.2.0 vgi-golf-http \
  --host 0.0.0.0 \
  --port 8000 \
  --cors-origins 'https://sql.example.com' \
  --log-format json
```

`vgi-golf-http` disables the description pages and cross-origin access unless
you turn them on. Terminate TLS at a trusted proxy or load balancer. Give
every replica the same `VGI_SIGNING_KEY`. Rotate the key in one coordinated
deployment, because it signs all outstanding state tokens.

Supported authentication options are:

- `VGI_BEARER_TOKENS`: comma-separated `token=principal` pairs.
- `VGI_JWT_ISSUER` and `VGI_JWT_AUDIENCE`: JWT/JWKS authentication.
- `VGI_PROXY_PROOF_MODE=require`, plus the matching VGI proxy-proof settings,
  when a trusted proxy authenticates users.

An intentionally public service can set `VGI_ALLOW_ANONYMOUS=1`. Put gateway
rate limits in front of it, because every query reaches the upstream sources.
The health probe is `GET /health`.

### Container

```bash
docker build -t vgi-golf .
docker run --rm -p 8000:8000 \
  -e VGI_SIGNING_KEY='replace-with-a-stable-random-secret' \
  -e VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn' \
  vgi-golf
```

The image runs as an unprivileged user and emits JSON logs. It disables the
interactive description pages and includes a health check.

## Reliability and operations

- **Timeouts:** each upstream request has a 30-second timeout (10 seconds to
  connect), and each query has a 120-second total deadline.
- **Retries:** a request gets at most four attempts. HTTP 429, transient 5xx
  responses and transport errors are retried with jittered exponential
  backoff. `Retry-After` is honored, up to 30 seconds per retry. Redirects
  are followed.
- **Concurrency:** each process sends at most four upstream requests at once.
- **Caching:** an in-process cache holds up to 256 responses. Each response is
  reused for the longer of the origin's `max-age` and a politeness floor: 60
  seconds for live leaderboards and scoreboards, 5 minutes for statistics,
  10 minutes for profiles and the world ranking, and 15 minutes for season
  schedules. ESPN advertises `max-age=1` on leaderboards, so without the floor
  the worker would poll every second. The same window goes to VGI clients as
  result-cache metadata.
- **Pagination:** statistics and rankings fetch every page, and a short page
  ends the loop. 404 responses for unknown players or scorecards return empty
  results.
- **Logging:** retries and transport failures are logged as warnings.
  Per-request timings are logged at debug level.

For hermetic tests, you can point the base URLs at alternate roots with
`GOLF_ESPN_SITE_URL`, `GOLF_ESPN_WEB_URL`, `GOLF_ESPN_CORE_URL` and
`GOLF_OWGR_URL`. Never set them from untrusted user input.

## Development and release checks

```bash
uv lock --check
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest            # offline: unit, catalog, packaging and hermetic Haybarn E2E
uv run pytest -m live    # opt-in: hits ESPN and OWGR
uv build
uvx twine check dist/*
uvx --from vgi-lint-check==2.5.0 vgi-lint lint 'uv run golf_worker.py' \
  --no-execute --no-check-links --audit-waivers --fail-on warning
uvx --from vgi-lint-check==2.5.0 vgi-lint lint 'uv run golf_worker.py' \
  --execute-concurrency 1 --no-check-links --audit-waivers --fail-on error
```

The offline suite replays trimmed real responses from `tests/fixtures/`. The
Haybarn end-to-end test serves those fixtures from a local HTTP server and runs
SQL against every current table through `INSTALL vgi FROM community`. The
scheduled live workflow runs the live tests and the executing lint one at a
time, to keep the load on upstream sources low.

Publishing is driven by releases. Create a GitHub release named `vX.Y.Z` that
matches the version in `pyproject.toml`. CI then builds, validates and attests
the wheel and sdist, attaches them to the release, and publishes a
multi-platform container image to `ghcr.io/query-farm/vgi-golf`. The package is
not published to PyPI; install it from the release tag as shown in the quick
start.

## License

MIT for the worker source code; see [LICENSE](LICENSE). Retrieved data stays
subject to its owners' terms, as described above.

Copyright 2026 Query Farm LLC - https://query.farm
