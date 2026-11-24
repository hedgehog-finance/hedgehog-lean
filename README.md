# Hedgehog LEAN

A separately downloadable LEAN runtime and open Gateway bridge. Gateway's main installer does not bundle the engine. Each runtime archive includes LEAN, Python, .NET, and the Gateway integration components. No QuantConnect account or Docker installation is required.

## Downloads

Native packages are published under [Releases](https://github.com/hedgehog-finance/hedgehog-lean/releases).

| Platform | Archive |
| --- | --- |
| macOS Apple Silicon | `lean-1.0.0-darwin-arm64.tar.gz` |
| macOS Intel | `lean-1.0.0-darwin-x64.tar.gz` |
| Windows x64 | `lean-1.0.0-win32-x64.tar.gz` |
| Linux x64 | `lean-1.0.0-linux-x64.tar.gz` |

Each archive has a matching `<platform>.json` manifest containing its version, upstream commit, byte length, and SHA-256. Verify the manifest and archive before installation. The release workflow publishes only after all four native builds and smoke tests succeed. Linux builds target Ubuntu 22.04; other distributions must satisfy .NET's native library requirements.

Gateway tries `download.ciweiai.com` first, then this repository's GitHub Release, then downloads the pinned official LEAN source and builds locally. Source builds require an existing compatible toolchain. A source archive is not an installable runtime archive.

## Source layout and provenance

- `vendor/lean/`: the complete upstream QuantConnect/Lean snapshot at commit `f696c556828e9b3ea6695cb4ac41ff233ba5f430`, including source, sample data, original copyright headers, and license.
- `src/quant/lean-host/`: the C# local market-data queue.
- `src/quant/bridge/`: the Python JSON-RPC bridge, market-data mapping, backtest host, and persistent paper-simulation host. Shared bridge mappings for external providers are included; their dependencies are not installed in the runtime.
- `scripts/build-lean-runtime.mjs`: source verification, explicit patching, native compilation, packaging, and manifest generation.
- `tests/smoke.py`: real LEAN backtests using the packaged Python and .NET, including fills/equity, minute timestamps, and strategy failure handling.
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
```

Official source archive SHA-256: `cd0eaaebe74a75ad4371206bfde93c645495050ca3bc353cddec2ee597a36203`. The build explicitly applies a Python shutdown GIL-lifetime patch and includes its explanation in the runtime archive.

The output contains a `lean-runtime/` directory, one platform archive, and its manifest. No user strategies, credentials, market databases, or Gateway business databases are uploaded. When mirroring a release to the CDN, upload archives first and manifests last. Use identical files on the CDN and GitHub.

## Scope

Independent `us-equity`, `cn-equity`, and `hk-equity` profiles support raw ordinary-equity OHLCV, share-based volume, daily/minute backtests, and local paper simulation. CN/HK execution requires an explicit versioned market-rules snapshot and per-bar trading status. See [Market adapters](docs/MARKETS.md) for the contract, execution assumptions, and limitations. It exposes no live brokerage execution. It does not automatically fetch user market data or start strategies. The runtime itself is not a strategy security sandbox; Gateway applies an additional process sandbox on supported platforms.

Hedgehog additions use [LICENSE](LICENSE). Upstream LEAN retains [vendor/lean/LICENSE](vendor/lean/LICENSE).
