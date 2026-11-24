独立 LEAN 运行包，覆盖 macOS Apple Silicon、macOS Intel、Windows x64、Linux x64。包含 Python 3.11.11、.NET 10 运行时、LEAN 与 Gateway 适配组件。

每个平台均在原生 GitHub runner 上构建，并用解压到新目录后的 Python/.NET 执行真实 LEAN 回测，核对成交、净值、分钟结束时间及策略失败状态。安装请同时使用对应 `.json` 清单校验文件大小和 SHA-256。

LEAN 基于 `f696c556828e9b3ea6695cb4ac41ff233ba5f430`，源码、构建脚本、补丁说明和依赖来源均已公开。首版限美股原始 OHLCV、USD、日/分钟行情；持续模拟需要显式推送行情。没有实盘交易接口。各平台安装后的完整 Gateway 沙箱及持续模拟仍需按产品发布流程验收，不将本次回测验证等同于实盘可用。
