Standalone LEAN runtimes for macOS Apple Silicon, macOS Intel, Windows x64, and Linux x64. Each package includes Python 3.11.11, the .NET 10 runtime, LEAN, and Gateway integration components.

Every target is built on a native GitHub runner. The workflow extracts each package into a new directory and executes real LEAN backtests using its packaged Python and .NET, checking fills, equity, minute bar-end timestamps, and strategy failure handling. Verify the byte length and SHA-256 against the matching `.json` manifest before installing.

LEAN is pinned to `f696c556828e9b3ea6695cb4ac41ff233ba5f430`. Source, build scripts, patch notes, and dependency provenance are public. The first adapter supports raw US-equity OHLCV data, USD, and daily/minute bars. Persistent paper simulation requires an explicit market-data feed. No live brokerage execution is exposed.

Full Gateway sandbox and persistent simulation acceptance on every platform remain part of the Gateway release process. These native backtest tests do not establish live-trading readiness.
