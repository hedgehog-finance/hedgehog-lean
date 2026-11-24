# Source and dependency provenance

Hedgehog additions use Apache-2.0. Third-party components retain their original licenses and copyright notices. Runtime archives retain Python package `.dist-info` license files, CPython `LICENSE.txt`, .NET `LICENSE.txt` and `ThirdPartyNotices.txt`, and `LEAN-LICENSE`.

| Component | Pinned version / source | License / notices |
| --- | --- | --- |
| LEAN | [f696c556828e9b3ea6695cb4ac41ff233ba5f430](https://github.com/QuantConnect/Lean/tree/f696c556828e9b3ea6695cb4ac41ff233ba5f430); complete snapshot in `vendor/lean` | Apache-2.0; upstream file headers retained |
| CPython | [3.11.11 source](https://www.python.org/ftp/python/3.11.11/Python-3.11.11.tar.xz) | PSF License, included `python/lib/python3.11/LICENSE.txt` or Windows `python/LICENSE.txt` |
| Portable Python build | [python-build-standalone](https://github.com/astral-sh/python-build-standalone), installed by uv for CPython 3.11.11 | Build recipes and bundled third-party licenses remain upstream; packaged `BUILD` identifies build revision |
| .NET runtime | [10.0.15 source](https://github.com/dotnet/runtime/tree/v10.0.15), built with SDK 10.0.404 | MIT and `dotnet/ThirdPartyNotices.txt` |
| pandas | [2.2.3](https://github.com/pandas-dev/pandas/tree/v2.2.3) | BSD-3-Clause |
| wrapt | [1.16.0](https://github.com/GrahamDumpleton/wrapt/tree/1.16.0) | BSD-2-Clause |
| Python.NET fork | QuantConnect.pythonnet 2.0.70, pinned in upstream project files | Upstream NuGet metadata and [QuantConnect/pythonnet](https://github.com/QuantConnect/pythonnet) |
| Other .NET packages | Exact resolved versions in packaged `engine/*.deps.json`; source declarations in `vendor/lean/**/*.csproj` | Each package's upstream license applies |
| Other Python packages | Installed versions and license files in packaged `python/**/site-packages/*.dist-info` | Each package's upstream license applies |

The build does not copy user configuration, accounts, credentials, market databases, model preferences, or Gateway business databases. The runtime is an independently built distribution, not an official QuantConnect binary release.

The only LEAN source modification is documented in `LEAN-PATCHES.txt`: remove the undisposed `Py.GIL()` handle in `PythonInitializer.Shutdown`; let `PythonEngine.Shutdown()` manage its GIL lifecycle. `Gateway.Quant.dll` is built from this repository's `src/quant/lean-host`.
