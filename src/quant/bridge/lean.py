"""Local LEAN launcher. Paper uses PaperBrokerage only; no real brokerage inputs."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import zipfile


def run(data, runtime_root, paper, emit):
    root, folder = Path(runtime_root).resolve(), Path.cwd()
    manifest = json.loads((root / "runtime.json").read_text())
    dataset = data["dataset"]
    adjustments = json.loads(dataset["adjustment_json"])
    # v1 explicitly uses LEAN's USA equity calendars and settlement models.
    if dataset["market"].lower() not in ("usa", "us") or adjustments.get("prices") != "raw" or adjustments.get("volumeUnit") != "shares" or adjustments.get("currency") != "USD":
        raise ValueError("LEAN v1 requires USA equities, raw prices and shares; other markets need a market adapter")
    symbols = json.loads(dataset["symbols_json"])
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,15}", symbol) for symbol in symbols):
        raise ValueError("Unsupported equity symbol")
    data_root = folder / "data"
    for name in ("market-hours", "symbol-properties"):
        shutil.copytree(root / "data" / name, data_root / name, dirs_exist_ok=True)
    times = []
    bar_ends = {}
    for symbol in symbols:
        rows = [bar for bar in data["bars"] if bar["symbol"] == symbol]
        grouped = {}
        for bar in rows:
            when = datetime.fromisoformat(bar["time"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
            available = datetime.fromisoformat(bar["availableAt"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
            # Raw equity bars must be available at their boundary; delayed observations
            # need a separate custom-data adapter, never silently backdate information.
            if available != when:
                raise ValueError("LEAN raw bars require time=availableAt")
            if when.second or when.microsecond:
                raise ValueError("LEAN bars must end on a minute boundary")
            times.append(when)
            bar_ends.setdefault(symbol, []).append(int(when.timestamp()))
            # Wire time is bar END. LEAN files encode bar START (daily files use
            # the trading date and LEAN's precise exchange close time).
            start = when.replace(hour=0, minute=0) if dataset["frequency"] == "daily" else when - timedelta(minutes=1)
            stamp = start.strftime("%Y%m%d %H:%M") if dataset["frequency"] == "daily" else str((start.hour * 3600 + start.minute * 60) * 1000)
            line = ",".join([stamp] + [str(round(bar[key] * 10000)) for key in ("open", "high", "low", "close")] + [str(bar["volume"])])
            key = symbol.lower() if dataset["frequency"] == "daily" else start.strftime("%Y%m%d") + "_trade"
            grouped.setdefault(key, []).append(line)
        directory = data_root / "equity/usa" / dataset["frequency"]
        if dataset["frequency"] != "daily":
            directory /= symbol.lower()
        directory.mkdir(parents=True, exist_ok=True)
        for key, lines in grouped.items():
            with zipfile.ZipFile(directory / (key + ".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(key + ".csv", "\n".join(lines) + "\n")
    settings = {"symbols": symbols, "barEnds": bar_ends, "frequency": dataset["frequency"], "cash": data["initialCash"], "commission": data["commission"], "slippage": data.get("slippage", 0),
                "start": min(times).date().isoformat(), "end": max(times).date().isoformat(), "paper": paper}
    (folder / "settings.json").write_text(json.dumps(settings))
    (folder / "candidate.py").write_text(data["code"])
    shutil.copy(Path(__file__).with_name("lean_algorithm.py"), folder / "main.py")
    audit = folder / "events.jsonl"
    audit.touch()
    feed = folder / "feed.jsonl"
    feed.touch()
    config = {
        "environment": "gateway", "algorithm-type-name": "GatewayHostAlgorithm", "algorithm-language": "Python",
        "algorithm-location": str(folder / "main.py"), "data-folder": str(data_root), "results-destination-folder": str(folder / "results"),
        "object-store-root": str(folder / "store"), "python-additional-paths": [str(folder), str(root / "engine")],
        "live-mode": paper, "live-mode-brokerage": "PaperBrokerage", "data-queue-handler": ["Gateway.Quant.FileDataQueue"],
        "gateway-feed-file": str(feed), "job-queue-handler": "QuantConnect.Queues.JobQueue", "messaging-handler": "QuantConnect.Messaging.Messaging",
        "api-handler": "QuantConnect.Api.Api", "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider", "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "alpha-handler": "QuantConnect.Lean.Engine.Alphas.DefaultAlphaHandler", "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "data-permission-manager": "QuantConnect.Lean.Engine.DataFeeds.DataPermissionManager",
        "setup-handler": "QuantConnect.Lean.Engine.Setup." + ("BrokerageSetupHandler" if paper else "BacktestingSetupHandler"),
        "result-handler": "QuantConnect.Lean.Engine.Results." + ("LiveTradingResultHandler" if paper else "BacktestingResultHandler"),
        "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds." + ("LiveTradingDataFeed" if paper else "FileSystemDataFeed"),
        "real-time-handler": "QuantConnect.Lean.Engine.RealTime." + ("LiveTradingRealTimeHandler" if paper else "BacktestingRealTimeHandler"),
        "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
        "history-provider": ["QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider"],
        "algorithm-id": "gateway", "debugging": False, "close-automatically": True,
        "environments": {"gateway": {}}, "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "live-cash-balance": [{"currency": "USD", "amount": data["initialCash"], "conversion-rate": 1}],
    }
    if manifest.get("pythonVenv"):
        config["python-venv"] = str(root / manifest["pythonVenv"])
    (folder / "config.json").write_text(json.dumps(config))
    env = dict(os.environ)
    env.update(PYTHONNET_PYDLL=str(root / manifest["pythonLibrary"]), PYTHONHOME=str(root / manifest["pythonHome"]),
               DOTNET_ROOT=str(root / "dotnet"), DOTNET_CLI_HOME=str(folder), DOTNET_CLI_TELEMETRY_OPTOUT="1")
    with (folder / "lean.log").open("wb") as log:
        child = subprocess.Popen([str(root / manifest["dotnet"]), str(root / "engine/QuantConnect.Lean.Launcher.dll"), "--config", str(folder / "config.json")],
                                 cwd=root / "engine", env=env, stdout=log, stderr=subprocess.STDOUT)
    def stop(*_args):
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    if paper:
        def push():
            for line in sys.stdin:
                batch = json.loads(line)
                with feed.open("a") as stream:
                    for bar in batch["bars"]:
                        stream.write(json.dumps(bar) + "\n")
        threading.Thread(target=push, daemon=True).start()
    with audit.open() as stream:
        while True:
            line = stream.readline()
            if line:
                event = json.loads(line)
                emit(event["type"], event["data"], event["time"])
            elif child.poll() is not None:
                break
            else:
                threading.Event().wait(0.1)
    if child.wait() != 0:
        raise ValueError("LEAN failed; inspect lean.log")
    # LEAN may exit normally after a user-code exception and still call
    # on_end_of_algorithm. A summary alone is not evidence of completion.
    if not paper:
        result_file = folder / "results/gateway.json"
        state = json.loads(result_file.read_text()).get("state", {}) if result_file.exists() else {}
        if state.get("RuntimeError") or state.get("Status") != "Completed":
            raise ValueError("LEAN did not complete successfully; inspect results/gateway.json and lean.log")
    summary = folder / "summary.json"
    if not summary.exists():
        raise ValueError("LEAN did not produce a verified completion report; inspect lean.log")
    return json.loads(summary.read_text())
