# Hedgehog LEAN

A separately downloadable LEAN runtime and open Gateway bridge. Gateway's main installer does not bundle the engine. Each runtime archive includes LEAN, Python, .NET, and the Gateway integration components. No QuantConnect account or Docker installation is required.

## Downloads

Native packages are published under [Releases](https://github.com/hedgehog-finance/hedgehog-lean/releases).

| Platform | Archive |
| --- | --- |
| macOS Apple Silicon | `lean-1.0.1-darwin-arm64.tar.gz` |
| macOS Intel | `lean-1.0.1-darwin-x64.tar.gz` |
| Windows x64 | `lean-1.0.1-win32-x64.tar.gz` |
| Linux x64 | `lean-1.0.1-linux-x64.tar.gz` |

Each archive has a matching `<platform>.json` manifest containing its version, upstream commit, byte length, and SHA-256. Verify the manifest and archive before installation. The release workflow publishes only after all four native builds and smoke tests succeed. Linux builds target Ubuntu 22.04; other distributions must satisfy .NET's native library requirements.

CI checks out the integration and build files, then downloads the checksum-pinned official source archive. It avoids fetching the same upstream source twice; the full vendored snapshot remains available in this repository for inspection.

Gateway tries `download.ciweiai.com` first, then this repository's GitHub Release. If both download sources are unavailable, it reports the failure and offers a reinstall action. Gateway does not automatically download source, install a build toolchain, or compile the engine. Developers can still build manually using the instructions below; a source archive is not an installable runtime archive.

Version 1.0.1 includes the [runtime audit fixes](RELEASE_NOTES.md#v101). Release publication requires successful native checks on all four targets; the older v1.0.0 archives remain unchanged.

## Source layout and provenance

- `vendor/lean/`: the complete upstream QuantConnect/Lean snapshot at commit `f696c556828e9b3ea6695cb4ac41ff233ba5f430`, including source, sample data, original copyright headers, and license.
- `src/quant/lean-host/`: the C# local market-data queue.
- `src/quant/bridge/`: the Python JSON-RPC bridge, market-data mapping, backtest host, and persistent paper-simulation host. Shared bridge mappings for external providers are included; their dependencies are not installed in the runtime.
- `scripts/build-lean-runtime.mjs`: source verification, explicit patching, native compilation, packaging, and manifest generation.
- `tests/smoke.py`: real LEAN backtests using the packaged Python and .NET, including fills/equity, minute timestamps, and strategy failure handling.
- `tests/lifecycle_smoke.py`: a real persistent paper engine, verified cancellation, and worker/engine cleanup after bridge process failure on each native platform.
- `tests/test_protocol.py`: durable cancellation, external worker configuration, and process ownership regressions; Windows APIs are mocked when running on another platform.
- `tests/feed-fixture/`: a .NET fixture using the actual file data queue, covering early subscriptions, partial writes, and minute bar timestamps.
- `.github/workflows/release.yml`: native builds and release publication for all four targets.
- [DEPENDENCIES.md](DEPENDENCIES.md): pinned dependencies, upstream source locations, and license information.

Third-party components retain their own licenses and authorship. The complete CPython and .NET upstream projects are available through pinned source links. This repository directly vendors LEAN and contains the Gateway integration source.

## Build locally

Requirements: Node.js 22+, .NET SDK 10.0.404, a relocatable CPython 3.11.11 distribution from python-build-standalone, and a separate Python environment containing pandas 2.2.3 and wrapt 1.16.0. Build on the target operating system and architecture.

```sh
npm ci
# Download the pinned official source; the builder verifies its SHA-256.
curl --fail --location 'https://codeload.github.com/QuantConnect/Lean/legacy.tar.gz/f696c556828e9b3ea6695cb4ac41ff233ba5f430' --output lean-source.tar.gz
node scripts/build-lean-runtime.mjs --source-archive lean-source.tar.gz --python /absolute/path/to/venv/python --dotnet /absolute/path/to/dotnet --output /absolute/path/to/new-output
python tests/smoke.py /absolute/path/to/new-output/lean-runtime
python tests/market_smoke.py /absolute/path/to/new-output/lean-runtime src/quant/bridge
python tests/lifecycle_smoke.py /absolute/path/to/new-output/lean-runtime
python -m unittest discover -s tests -p test_protocol.py
dotnet run --project tests/feed-fixture/FeedFixture.csproj -p:LeanRoot=/absolute/path/to/new-output/lean-runtime/engine
```

Official source archive SHA-256: `cd0eaaebe74a75ad4371206bfde93c645495050ca3bc353cddec2ee597a36203`. The build explicitly applies a Python shutdown GIL-lifetime patch and includes its explanation in the runtime archive.

The output contains a `lean-runtime/` directory, one platform archive, and its manifest. No user strategies, credentials, market databases, or Gateway business databases are uploaded. When mirroring a release to the CDN, upload archives first and manifests last. Use identical files on the CDN and GitHub.

## Scope

Independent `us-equity`, `cn-equity`, and `hk-equity` profiles support raw ordinary-equity OHLCV, share-based volume, daily/minute backtests, and local paper simulation. CN/HK execution requires an explicit versioned market-rules snapshot and per-bar trading status. See [Market adapters](docs/MARKETS.md) for the contract, execution assumptions, and limitations. It exposes no live brokerage execution. It does not automatically fetch user market data or start strategies. The runtime itself is not a strategy security sandbox; Gateway applies an additional process sandbox on supported platforms.

Hedgehog additions use [LICENSE](LICENSE). Upstream LEAN retains [vendor/lean/LICENSE](vendor/lean/LICENSE).
