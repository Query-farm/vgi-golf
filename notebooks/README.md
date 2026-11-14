# Golf: the holes behind a round total

[Open the executed notebook](01_holes_behind_the_round_total.ipynb). It reconciles
Scottie Scheffler's 2026 Masters scorecard, joins course setup with the full key,
and compares total scoring relative to par with the average per played hole.
The experiment narrows the window to rounds three and four.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --frozen
uv run python execute.py
```

The locked environment uses Python 3.13–3.14 and Haybarn 2.0.0rc11. The visible
SQL location is `uvx --python 3.13 --with vgi-python==0.51.1 --from
git+https://github.com/Query-farm/vgi-golf@v0.2.0 vgi-golf` (one line in SQL).
It downloads the published Git release into an isolated environment. No worker
checkout, local worker build, API key or registered kernel is required. First use
needs access to GitHub, package indexes and community extensions.

For an editor, register and select this environment:

```bash
uv run python -m ipykernel install --user --name golf-examples \
  --display-name "Python (vgi-golf notebooks)"
```

Restart and run all after editing parameters. SQL and parameters are visible;
setup and plotting source are folded. The folder includes its assets, helpers and
fixtures and runs after being copied outside the repository.

## Experiment and live verification

```bash
uv run python execute.py --parameters '{"MIN_ROUND":3}' \
  --output-dir validation/weekend
uv run python execute.py --parameters '{"DATA_MODE":"live"}' \
  --output-dir validation/live
```

`MIN_ROUND` accepts integers 1–4 and includes that round through four. The default
is 1; the experiment uses 3. The complete-source reconciliation remains unchanged
while the selected window shrinks. Means need not move in a particular direction.
Restore 1 for the default. Event 401811941, player 9478 and tour `pga` are fixed.

The runner uses fresh kernels and saves only after success. Overrides require a
separate destination, which receives execution, fixture and branding companions.
Generated logs and exported charts are ignored.

## Recorded provenance

Recorded mode serves two **unmodified repository test fixtures** through the real
worker's supported URL overrides: `leaderboard_pga_masters_2026.json` and
`linescores_masters_scheffler.json`. They come from `tests/fixtures/` at public
revision `a2253a7cde897b2c0dcadcfe12eaa7b5f48708d5`; tests describe them as recorded
upstream responses. Their original capture timestamps are not documented. Exact
paths, lengths and SHA-256 hashes are in [provenance.json](fixtures/provenance.json),
and the local server verifies each hash before starting.

The leaderboard is an **eight-player subset**, not the complete tournament field.
The selected player's scorecard includes 72 regulation holes, and course setup
contains 18 holes. The notebook uses only that player's leaderboard row for
reconciliation and never treats the subset as a field denominator. The run clock
is explicitly distinct from fixture capture time.

The local server accepts only the documented event/player routes. Setup clears all
four inherited worker endpoint overrides. In recorded mode every root points to
loopback; in live mode the overrides are absent. Live queries go to ESPN's public,
unofficial endpoints for the same historical event and never fall back to fixtures.
Endpoint failures or upstream corrections can stop or change a live run.

## Interpretation and source limits

Every selected score is checked against its par, round total and leaderboard
total. Course joins use event ID, course ID and hole number; missing or duplicated
matches stop execution. Negative values mean under par, positive mean over par,
and zero means a completed par. Missing scores are not converted to zero.

The par-group mean uses played holes as its denominator. Total and mean panels
have separate units. These observations describe one player and window; they
cannot establish field difficulty, causal explanations or future performance.
Course yardage is the reported setup, not a verified daily tee location.

ESPN owns the upstream data; the worker's MIT licence covers its code. This
example does not imply affiliation with ESPN, the tours or Augusta National.
The [repository source notes](https://github.com/Query-farm/vgi-golf/blob/a2253a7cde897b2c0dcadcfe12eaa7b5f48708d5/README.md#data-sources-licence-and-affiliation)
describe the undocumented endpoints and upstream usage terms. See also
[ESPN's event leaderboard](https://www.espn.com/golf/leaderboard/_/tournamentId/401811941).
To upgrade, change the literal Git release pin and verify recorded default,
experiment and live runs. [STANDARD.md](STANDARD.md) records notebook conventions.
