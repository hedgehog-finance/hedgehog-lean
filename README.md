# Hedgehog LEAN

为 Hedgehog Gateway 提供独立下载的 LEAN 运行环境与开放适配源码。Gateway 主安装包不包含大型引擎；运行包包含 LEAN、Python、.NET 运行时与 Gateway 适配组件，不需要 QuantConnect 账号或 Docker。

## 下载

正式安装包位于 [Releases](https://github.com/hedgehog-finance/hedgehog-lean/releases)。发布流水线覆盖：

| 平台 | 文件名 |
| --- | --- |
| macOS Apple Silicon | `lean-1.0.0-darwin-arm64.tar.gz` |
| macOS Intel | `lean-1.0.0-darwin-x64.tar.gz` |
| Windows x64 | `lean-1.0.0-win32-x64.tar.gz` |
| Linux x64 | `lean-1.0.0-linux-x64.tar.gz` |

每个安装包附带 `<platform>.json`，记录版本、来源 commit、字节数和 SHA-256。下载后必须验证清单与内容。四个平台全部构建和测试成功后，流水线才创建公开 Release；不发布占位文件。Linux 在 Ubuntu 22.04 构建；其他发行版仍需满足 .NET 原生依赖要求。

Gateway 下载顺序：`download.ciweiai.com` → 本仓库 GitHub Release → 官方固定版本 LEAN 源码并本机构建。第三种方式需要本机已具备构建依赖，失败会显示原因。源码压缩包不能冒充运行包。

## 源码与来源

- `vendor/lean/`：QuantConnect/Lean 固定 commit `f696c556828e9b3ea6695cb4ac41ff233ba5f430` 的完整上游源码和示例数据快照，保留原许可证与署名。
- `src/quant/lean-host/`：本地行情队列 C# 组件。
- `src/quant/bridge/`：Python JSON-RPC 适配桥、行情映射、回测与持续模拟宿主。通用桥中保留外部工具映射，但运行包不会安装这些工具。
- `scripts/build-lean-runtime.mjs`：构建、补丁、运行时打包和校验清单生成。
- `tests/smoke.py`：使用打包后的 Python 和 .NET 验证真实 LEAN 日线回测、分钟时间戳和策略失败。
- `.github/workflows/release.yml`：四平台原生构建与发布。
- [DEPENDENCIES.md](DEPENDENCIES.md)：Python、.NET、第三方库的固定版本、上游源码和许可证来源。

第三方运行时保留其各自上游许可证；它们不是 Hedgehog 原创源码，也不重新许可。CPython/.NET 的完整上游工程通过固定版本源码链接取得；本仓库直接保存 LEAN 和 Gateway 适配源码。

## 本机构建

要求 Node.js 22+、.NET SDK 10.0.404、可迁移的 CPython 3.11.11（python-build-standalone）、独立 Python 环境中的 pandas 2.2.3 与 wrapt 1.16.0。构建必须在目标操作系统及处理器上进行，不通过修改文件名冒充跨平台包。

```sh
npm ci
# 下载官方固定版本归档，构建脚本会验证 SHA-256。
curl --fail --location 'https://codeload.github.com/QuantConnect/Lean/legacy.tar.gz/f696c556828e9b3ea6695cb4ac41ff233ba5f430' --output lean-source.tar.gz
node scripts/build-lean-runtime.mjs --source-archive lean-source.tar.gz --python /absolute/path/to/venv/python --dotnet /absolute/path/to/dotnet --output /absolute/path/to/new-output
python tests/smoke.py /absolute/path/to/new-output/lean-runtime
```

官方归档 SHA-256：`cd0eaaebe74a75ad4371206bfde93c645495050ca3bc353cddec2ee597a36203`。源码中的退出 GIL 生命周期补丁由构建脚本显式应用，说明随运行包一起分发。

构建结果包含一个 `lean-runtime/` 目录、一个平台归档和一个清单。运行、策略、交易记录和凭据不随构建上传。发布到 CDN 时先上传归档，再上传清单；CDN 与 GitHub 应使用同一套文件。

## 能力边界

首版支持美股原始 OHLCV、USD、股数成交量，日线和分钟线回测及持续本地 Paper 模拟。没有实盘交易接口，不自动下载用户行情或启动策略。运行包自身不是策略安全沙箱；Gateway 在支持的平台上额外施加进程沙箱。

本仓库的许可证见 [LICENSE](LICENSE)，上游 LEAN 的许可证见 [vendor/lean/LICENSE](vendor/lean/LICENSE)。
