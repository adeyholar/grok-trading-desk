# grok-trading-desk

Equity trading desk orchestrated by Grok (Alpaca paper). Fail-closed agents with
a strict JSON contract and pessimistic fallbacks. **This fork targets Options
Paper Desk next**; phase 0 removed the former memecoin / pump.fun / Solana side.

The design principle throughout: **a model that fails is a model that says no.**
An unreachable checker rejects. A broken exit manager holds. A dead allocator
keeps 100% equities. Nothing about a failure looks like permission.

Every agent answers under a strict JSON schema, reads live web/X/news data
through xAI's `search_parameters`, and reports what it cost. Decisions that
close are fed back into later prompts. See [RESEARCH.md](RESEARCH.md).

---

## Architecture

```
                          ┌─────────────────────────────┐
                          │          desk.py            │
                          │   3 concurrent asyncio loops│
                          └──────────────┬──────────────┘
               ┌─────────────────────────┼────────────────────┐
               │                         │                    │
        ╔══════▼══════╗           ╔══════▼══════╗     ╔═══════▼══════╗
        ║ stock_loop  ║           ║  exit_loop  ║     ║allocator_loop║
        ║ 1/min, RTH  ║           ║   every 4h  ║     ║   every 24h  ║
        ╚══════╤══════╝           ╚══════╤══════╝     ╚═══════╤══════╝
               │                         │                    │
          ┌────▼─────┐                   │               ┌────▼─────┐
          │ screener │                   │               │ allocator│
          │  (code)  │                   │               │ 100% eq. │
          └────┬─────┘                   │               └──────────┘
               │                         │
     ┌─────────┼─────────┬──────────┐    │
     │ analyst │  radar  │ insider  │    │
     │  (fast) │ (fast)  │  (fast)  │    │
     └────┬────┴────┬────┴────┬─────┘    │
          │         │         │          │
     ┌────▼─────────▼─────────▼───┐      │
     │ market_pulse (30m cache)   │      │
     └────┬───────────────────────┘      │
          │                              │
     ┌────▼────────────────────────┐     │
     │ stock_scoring (hard vetoes) │     │
     └────┬────────────────────────┘     │
          │                              │
     ┌────▼────────────────────────┐  ┌──▼───────────────┐
     │ stock_checker (deep, adv.)  │  │ exit_manager     │
     └────┬────────────────────────┘  │ HOLD/TIGHTEN/    │
          │                           │ TRIM/CLOSE       │
     ┌────▼────────────────────────┐  └──────────────────┘
     │ stock_executor (Alpaca)     │
     └─────────────────────────────┘
        └──── shared/risk.py — fail-closed equity limits ────┘
        └──── shared/log.py — append-only JSONL ─────────────┘
```

Four-layer equity path: **screener (code) → analysts (fast) → scoring vetoes
(code) → adversarial checker (deep) → executor**. Risk and exits sit outside
that path and cannot be negotiated with by a prompt.

---

## The bots

**screener** (`stocks/screener.py`, code) — Once per trading day. Composes
Alpaca movers / most-actives, snapshots, and daily bars. Filters on price,
volume, relative volume and gap. Market cap and sector have no Alpaca source, so
those thresholds are skipped when missing.

**analyst** (`stocks/analyst.py`) — Fundamentals and technicals in one call.

**radar** (`stocks/radar.py`) — Two weeks of news and sentiment.
`controversy > 0.7` is a hard veto (fallback sets it to 1.0).

**insider** (`stocks/insider.py`) — Form 4 / 13F flow. Cluster buying is a bonus;
heavy selling with weak buying is a hard veto.

**market_pulse** (`stocks/market_pulse.py`, 30-min cache) — Equity regime.
`go_signal < 0.3` pauses the whole book.

**allocator** (`shared/allocator.py`) — Confirms the stocks-only budget (always
100% equities in this fork).

**stock_checker** (`stocks/stock_checker.py`, **deep model**) — Adversarial gate
before money moves. Approves only when it cannot construct a plausible loss.

**exit_manager** (`shared/exit_manager.py`) — Every 4 hours. HOLD / TIGHTEN /
TRIM / CLOSE. Failure returns HOLD.

---

## Models, live data and cost

| tier | model | who |
|---|---|---|
| `fast` | `grok-4.3`, `reasoning_effort: none` | generators |
| `deep` | `grok-4.6` | stock checker |

**Live search is not optional.** Without `search_parameters`, the model acquires
no data (Grok 4.6 cutoff: 2026-02-01). Each agent declares its own `SEARCH`
policy. Spend comes from `usage.cost_in_usd_ticks`.

---

## Quick start

```bash
git clone https://github.com/adeyholar/grok-trading-desk.git
cd grok-trading-desk

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
$EDITOR config.yaml          # xAI key, Alpaca keys, risk limits

# decide and log everything, execute nothing
python -m src.desk --config config.yaml --dry-run

python scripts/dashboard.py --log logs/desk.jsonl
python scripts/replay.py    --log logs/desk.jsonl --days 7
```

```bash
pytest -v
```

### Going live

Paper trading is the default unless **both** hold:

```yaml
mode: "live"
```

```bash
python -m src.desk --config config.yaml --i-understand-the-risk
```

---

## Configuration

`config.yaml` is gitignored; only `config.example.yaml` is in the repo.

| Section | What it controls |
|---|---|
| `mode` | `paper` or `live` (live also needs the CLI flag) |
| `grok` | API key, models, live search, timeout, retries |
| `alpaca` | Key, secret, paper flag |
| `options` | Phase 1 Level-2 dry-run (DTE, max_risk, paper base URL) |
| `risk` | Budget, daily loss limit, open caps, sector cap, sizing |
| `stock_filter` | Screener thresholds |
| `scoring_weights` | Equity matrix + `min_score_to_buy` |
| `pulse` | Cache window, `min_go_signal` |
| `exits` | Loop interval, default stop/target, trim fraction |
| `market_hours` | Timezone and RTH window |
| `debate` | Bull/bear stage (off by default) |
| `memory` | Outcome recall |
| `logging` | JSONL path, cost-report interval |

### Risk (fail-closed)

`shared/risk.py` enforces daily loss, open-position and sector caps, and sizes
each trade by the tightest of: 15% of equity budget, 25% of remaining loss room,
and free budget — then scales by checker confidence.

### Hard vetoes live in code

- `controversy > 0.7` → skip
- `insider_selling > 0.8` **and** `insider_buying < 0.2` → skip
- `pulse.go_signal < 0.3` → equity book paused

### Outcome memory

`shared/memory.py` joins `buy` to `close` and injects comparable past trades into
the checker, exit manager and allocator. Analysts get no memory on purpose.

---

## Logging

| type | fields |
|---|---|
| `buy` | market, symbol, score, all_agent_scores, amount, tx_id |
| `skip` | market, symbol, reason, detail |
| `close` | market, symbol, pnl, hold_time |
| `action` | symbol, action, reason |
| `allocation` | stocks_pct, reason |
| `cost` | cumulative spend / calls / cache |

Broker refusals get distinct skip reasons: `pdt_blocked`, `wash_trade_blocked`,
`insufficient_buying_power`, `asset_not_tradable`, `broker_rate_limited`.

---


---

## Phase 1 — Options paper dry-run (Level 2)

Alpaca **paper only** (`https://paper-api.alpaca.markets`). Level 2 means **buy call /
buy put** — no sell-to-open, no multi-leg. The product-side **Adeola Go** gate is
documented but not enforced in code yet; Phase 1 defaults to dry-run (log only).

### Env / credentials

Prefer these over `alpaca.api_key` / `alpaca.api_secret` in yaml (values never printed):

- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_API_SECRET`

Config block (`options:` in `config.example.yaml`): `max_level: 2`, `dte_min` /
`dte_max` (default 14–42 ≈ 2–6 weeks), `max_risk_usd`, `dry_run: true`,
`base_url` locked to the paper host.

### Run dry-run

```bash
# screener survivors → overlay → checker stub → dry-run order log
python -m src.options --config config.yaml
# or
python -m src.desk --config config.yaml --options-pass

# optional symbols without screener
python -m src.options --config config.yaml --symbols AAPL,MSFT

# paper POST only if you really mean it (still refuses live host)
python -m src.options --config config.yaml --submit-paper
```

Package layout: `src/options/contracts.py`, `overlay.py`, `options_executor.py`,
`options_loop.py`. Hard validators reject mleg / sell-to-open / level > 2.

## Phase 1 — Stock Intraday + Phase 3 — Crypto Intraday

Same-session **intraday** books (not multi-day "Day" holds). **Worker discretion**
(Scanner / Context / Checker / Trade Desk) informed by evidence cites — Living Log
Lesson, reviewed journal, market facts — **not gut-only**. Adeola locks only:
Go window / No-Go / risk-caps / overnight hold. Flexible to market fluidity; **no
rigid time-exit automation**. Hard discipline: defined risk every ticket,
High→Checker→Go, max 1–2 opens/book, force No-Go after 2 losses, paper host only,
dry-run default, never invent fills. Living Log after review.

```bash
# Stock Intraday dry-run (mega-cap + ETF universe, marketable limit, defined risk)
python -m src.stocks.intraday --config config.yaml --symbols AAPL,SPY --price 200

# Crypto Intraday dry-run (Alpaca paper BTC/USD ETH/USD only — no pump.fun)
python -m src.crypto_intraday --config config.yaml --btc-price 60000 --eth-price 3000

# Paper POST only with Adeola Go (still refuses live host)
python -m src.stocks.intraday --config config.yaml --symbols SPY --price 500 --submit-paper --adeola-go
```

Options: **Mode Week** = existing 2–6w swing overlay; **Mode Intraday** = same-session
exit preference (0DTE locked unless Adeola unlocks). See `src/options/modes.py`.

Env (all lanes): `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET`.

## Disclaimer

Experimental software. Nothing here is financial advice. It ships paper-first
for a reason. You are responsible for your own losses and for whatever your
jurisdiction has to say about automated trading.
