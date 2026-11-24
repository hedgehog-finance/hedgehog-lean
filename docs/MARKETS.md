# Equity market adapters

The market adapter version is `1.0.0`. Three independent profiles live under `src/quant/bridge/markets`: `us-equity`, `cn-equity`, and `hk-equity`. Gateway owns data ingestion, user configuration, and durable results. These modules own engine-facing market rules and execution modeling. They use LEAN extension points; no additional engine fork is required.

| Profile | Currency / time zone | Model |
| --- | --- | --- |
| `us-equity` | USD / America/New_York | Existing LEAN US-equity calendar and default fill behavior |
| `cn-equity` | CNY / Asia/Shanghai | Ordinary cash A-shares on SSE, SZSE, or BSE; same-day purchases are unavailable for selling |
| `hk-equity` | HKD / Asia/Hong_Kong | Ordinary cash HKEX equities; same-day selling is allowed; security-specific board lots |

Each run covers one market profile and account currency. Mixed-currency portfolios and FX conversions are not implemented. ETFs, bonds, derivatives, short selling, margin lending, auction matching, and corporate actions are outside the CN/HK model. The input must explicitly assert a corporate-action-free interval. These restrictions are assumptions of this adapter, not a claim about everything an exchange supports.

## Immutable rule snapshot

CN/HK callers supply `marketRules` when creating a Gateway dataset. Gateway stores the snapshot in the existing dataset metadata and hashes it together with the bars; it does not create another database table. The bridge reads it from `dataset.adjustment_json.marketRules`.

The following is **synthetic test configuration**, not a current exchange calendar or fee schedule:

```json
{
  "profile": "cn-equity",
  "version": "my-provider-rules-2024-01",
  "validFrom": "2024-01-01",
  "validUntil": "2024-01-31",
  "corporateActions": "none-in-window",
  "cashAccounting": "trade-date",
  "calendar": {
    "version": "my-calendar-2024-01",
    "sessions": [
      { "start": "09:30", "end": "11:30" },
      { "start": "13:00", "end": "15:00" }
    ],
    "holidays": ["2024-01-01"],
    "earlyCloses": {}
  },
  "securities": [
    {
      "symbol": "600519",
      "kind": "stock",
      "exchange": "sse",
      "lotSize": 100,
      "minBuyQuantity": 100,
      "quantityStep": 100,
      "tickBands": [{ "upTo": 10000, "tick": 0.01 }]
    }
  ],
  "feeVersion": "my-fees-2024-01",
  "fees": [
    { "name": "example-sell-fee", "side": "sell", "rate": 0.0005, "minimum": 0, "roundingUnit": 0.01 }
  ]
}
```

- Dates bound the validity of **all** supplied rules. Split datasets when rules or security properties change. Supply authoritative historical values from your data provider; do not reuse current rules for an earlier period.
- Calendar sessions use local `HH:mm` times; holidays use ISO dates; `earlyCloses` maps dates to a local closing time. Weekends are closed. Daily bars must end at the declared close; minute bars must end within an open session. Both `time` and `availableAt` must identify that same bar boundary with an explicit timezone.
- Symbols are six digits for A-shares and five zero-padded digits for Hong Kong. Exchanges are `sse`, `szse`, `bse`, and `hkex` respectively. Trading quantities are in shares, not lots.
- `minBuyQuantity` and `quantityStep` express the applicable historical order minimum/increment. HK orders must be multiples of the security's `lotSize`; HK odd-lot execution is not modeled. CN can sell the full remaining eligible holding even when it is a residual lot.
- Tick bands are ordered, inclusive upper price bounds. They must cover every input and execution price. Buy prices round upward and sell prices downward to a valid tick.
- Fee components apply to `buy`, `sell`, or `both`. Each component applies its rate and minimum, then rounds upward to `roundingUnit`. Components are added to the run's proportional `commission`. Set `commission` to zero if the components already include brokerage commission. Rates are explicit historical inputs, never silently guessed.

Every CN/HK bar also carries `tradingStatus`: `normal`, `halted`, or `restricted`. CN bars require both `limitUp` and `limitDown`, or explicit `null` values for an unrestricted day. Bounds must agree with OHLC. These inputs capture historical board/status/listing exceptions without baking a universal percentage into code. HK callers may also supply bounds for restricted intervals.

## Execution assumptions

The CN/HK model executes market intents at the **next observed eligible bar close**, with the configured slippage and tick rounding. It will not fill using the same close that produced the signal. LEAN's internally generated opening/closing proxies are normalized to this model; explicit auction helper calls and limit/stop orders are rejected. This is an OHLCV simulation model, not exchange order-book matching.

Halted/restricted bars, zero volume, price-limit-blocked directions, and orders larger than observed bar volume do not fill. Slippage-adjusted prices must remain inside the observed high/low range. It uses whole fills rather than inventing queue positions or partial-fill liquidity. An order may remain pending until an eligible bar or explicit cancellation.

Buy orders require sufficient cash including modeled fees. Sell orders cannot create a short position. CN purchases become sellable on a later trading date; HK purchases can be resold the same day. Cash accounting is explicitly trade-date accounting: it does not model clearing, withdrawal availability, or legal custody settlement. Unfilled orders do not count as executed trades.

Backtests and paper simulation share the same rules, fill model, and fee logic. Paper feeds are validated against the same snapshot and current-time requirements before they reach the engine. Refresh the rule snapshot by creating a new dataset/session when its validity ends; do not mutate a running session's rules.

Reports record `marketProfile`, `marketAdapterVersion`, `marketRulesVersion`, currency, and modeling assumptions. Gateway retains the dataset hash so the calendar, fee version, security properties, and trading-status inputs are reproducible.

## Verification

`tests/test_market_profiles.py` covers calendars, lunch breaks, half-day closes, timestamp availability, price limits, CN sellability, residual quantities, HK board lots, tick bands, and fee rounding. `tests/market_smoke.py` runs actual LEAN daily/minute strategies and checks fills, currencies, next-bar timing, CN/HK sellability, halts/restrictions/limits, and rejection of missing trading status. All four native release jobs run these tests on relocated packages.

These tests use synthetic market inputs. They do not certify a user's data provider, every historical exchange rule, or live brokerage execution.

Model extension references: [LEAN market hours](https://www.quantconnect.com/docs/v2/writing-algorithms/securities/market-hours), [security properties](https://www.quantconnect.com/docs/v2/writing-algorithms/securities/key-concepts), and [reality modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts).
