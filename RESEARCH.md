# Research notes

What the upstream docs and prior art actually say, and what each finding changed
in this repo. Dated 2026-08-30. Updated 2026-09-27 for the equity-only
(Options Paper Desk) fork — memecoin / pump.fun / Solana paths removed in phase 0.

## xAI API

Source of truth: `https://docs.x.ai/openapi.json` (the rendered docs lag it).

### Model slugs retired 2026-05-15

`grok-4-fast-reasoning`, `grok-4-fast-non-reasoning`, `grok-4-0709`, `grok-3`,
`grok-code-fast-1` and three others were retired. Requests to a retired slug are
**auto-redirected and billed at `grok-4.3` rates**.

Post-retirement both generator and checker slugs landed on the same model, so
"the checker runs a stronger model" had stopped being true.

Current lineup and pricing (per 1M tokens, <200k context tier):

| model | context | in | cached in | out |
|---|---|---|---|---|
| grok-4.6 | 500k | $2.00 | $0.50 | $6.00 |
| grok-4.5 | 500k | $2.00 | $0.30 | $6.00 |
| grok-4.3 | 1M | $1.25 | $0.20 | $2.50 |

Now: generators run `grok-4.3` at `reasoning_effort: none`, the stock checker
runs `grok-4.6`. `reasoning_effort` is **only supported by `grok-4.3`**.

### Live search — the agents had no data

`ChatRequest.search_parameters`, verbatim from the spec:

> Set the parameters to be used for searched data. **If not set, no data will be
> acquired by the model.**

Grok 4.6's knowledge cutoff is 2026-02-01. Without retrieval, radar / insider /
market_pulse answered from a months-old prior. Each agent now declares its own
`SEARCH` policy; citations are logged with the decision.

### Structured outputs

`response_format: {"type": "json_schema", "json_schema": {"name", "schema",
"strict": true}}` is supported. Every agent ships a strict schema.

### Endpoint choice

`/v1/chat/completions` still carries everything this desk needs
(`response_format`, `reasoning_effort`, `search_parameters`, `prompt_cache_key`).

### Cost and cache accounting

`usage` carries `cost_in_usd_ticks` (USD = ticks / 1e10), `num_sources_used`,
`prompt_tokens_details.cached_tokens`. Prompts are built static-prefix-first
with a stable `prompt_cache_key`.

## Alpaca

The movers endpoint returns `Mover{symbol, percent_change, change, price}` and
most-actives returns `ActiveStock{symbol, volume, trade_count}`. **Neither carries
sector, market cap, or average volume.**

Fix: the screener composes most-actives + movers, the **snapshot** endpoint for
real previous close and same-day volume, and 20 daily bars for true `avg_volume`.
Market cap and sector filters are skipped when the datum is absent.

Execution constraints:

- bracket orders **cannot** be fractional and do not support extended hours;
  `time_in_force` must be DAY or GTC.
- HTTP **403** on submit usually means PDT protection on an under-$25k account;
  logged as `pdt_blocked`.

## Prior art

- **TradingAgents** (arXiv 2412.20138) — analyst team, bull/bear debate, risk
  override. Structured outputs + natural language.
- **FinMem** (2311.13743) / **TradingGPT** (2309.03736) — layered memory.
- **FinCon** (2407.06567) — conceptual verbal reinforcement.

`shared/memory.py` retrieves comparable past trades and injects outcomes into
the checker, exit manager and allocator. A bull/bear debate stage is off by
default (`debate.enabled`).

## Sources

- https://docs.x.ai/openapi.json
- https://docs.x.ai/developers/migration/may-15-retirement
- https://docs.x.ai/docs/guides/structured-outputs
- https://docs.x.ai/developers/tools/x-search
- https://docs.x.ai/developers/models
- https://alpaca.markets/sdks/python/api_reference/data/models.html
- https://alpaca.markets/sdks/python/api_reference/data/stock/screener.html
- https://docs.alpaca.markets/us/docs/fractional-trading
- https://alpaca.markets/learn/how-to-fix-common-trading-api-errors-at-alpaca
- https://arxiv.org/abs/2412.20138
