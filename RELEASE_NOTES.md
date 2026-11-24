## v1.0.1

Runtime and market-adapter patch release. The release workflow builds and verifies macOS Apple Silicon, macOS Intel, Windows x64, and Linux x64 independently before publication. Existing v1.0.0 assets remain unchanged.

- Persist a cancellation tombstone before acknowledging an absent task. A delayed submit with the same identity cannot start work, including after a bridge restart.
- Validate the account currency after candidate initialization for every market profile. Share the available bar volume across all orders for each CN/HK security and bar.
- Keep early paper-feed data until subscriptions exist, and buffer partial file writes until their newline commit.
- Cancel owned Windows worker process trees, including the .NET child. Monitor the parent through a process handle rather than its retained PID, and stop the worker's own tree when the owner exits. The release gate exercises cancellation and parent-exit cleanup with a real paper engine on every target.
- Preserve external providers' user configuration directories, while keeping LEAN jobs isolated. Set worker UTF-8 explicitly and force cleanup after malformed worker output.
- Preserve Windows system environment keys regardless of casing, and isolate the engine's stdin from the worker's feed pipe.

Native release checks cover real US/CN/HK backtests, account-currency rejection, aggregate bar-volume limits, paper initialization/cancellation and owner-exit cleanup, partial file-feed writes, and protocol regressions. See README for local test commands. No new Gateway business tables are required; cancellation tombstones use the existing bridge journal. Full Gateway application sandbox acceptance outside macOS Apple Silicon and continuous market-hours execution remain separate checks.

## v1.0.0

Standalone LEAN runtimes for macOS Apple Silicon, macOS Intel, Windows x64, and Linux x64. Each package includes Python 3.11.11, the .NET 10 runtime, LEAN, and Gateway integration components.

Every target is built on a native GitHub runner. The workflow extracts each package into a new directory and executes real LEAN backtests using its packaged Python and .NET, checking fills, equity, minute bar-end timestamps, and strategy failure handling. Verify the byte length and SHA-256 against the matching `.json` manifest before installing.

LEAN is pinned to `f696c556828e9b3ea6695cb4ac41ff233ba5f430`. Source, build scripts, patch notes, and dependency provenance are public. Independent US, China A-share, and Hong Kong cash-equity profiles support raw daily/minute OHLCV in USD, CNY, and HKD. CN/HK adapters require versioned calendars, security rules, fee components, and per-bar trading status. They enforce sellable-share constraints, whole-share/board-lot rules, price bands, and conservative next-observed-bar-close execution. See docs/MARKETS.md for limitations. Persistent paper simulation requires an explicit market-data feed. No live brokerage execution is exposed.

Full Gateway sandbox and persistent simulation acceptance on every platform remain part of the Gateway release process. These native backtest tests do not establish live-trading readiness.
