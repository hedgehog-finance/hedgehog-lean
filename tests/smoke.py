"""Execute the relocated runtime, never the build host's Python or .NET."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

runtime = Path(sys.argv[1]).resolve()
manifest = json.loads((runtime / "runtime.json").read_text())
python = str(runtime / manifest["python"])
bridge = runtime / "bridge/worker.py"
prefix = 'from AlgorithmImports import *\nclass GatewayStrategy(QCAlgorithm):\n    def initialize(self):\n        self.add_equity("SPY", Resolution.DAILY)\n'
bars = [{"symbol": "SPY", "time": f"2024-01-{day:02}T21:00:00Z", "availableAt": f"2024-01-{day:02}T21:00:00Z", "open": 100+i, "high": 102+i, "low": 99+i, "close": 101+i, "volume": 100000} for i, day in enumerate((2, 3, 4, 5, 8, 9, 10))]

def execute(code, minute=False, failure=False):
    data = {"code": code, "bars": bars, "dataset": {"market": "us", "frequency": "minute" if minute else "daily", "symbols_json": '["SPY"]', "adjustment_json": json.dumps({"prices": "raw", "volumeUnit": "shares", "currency": "USD"})}, "initialCash": 100000, "commission": 0.001, "slippage": 0}
    if minute:
        data["bars"] = [dict(bars[0], time=f"2024-01-02T14:{m}:00Z", availableAt=f"2024-01-02T14:{m}:00Z") for m in (31, 32, 33)]
    with tempfile.TemporaryDirectory(prefix="lean-release-test-") as directory:
        folder = Path(directory)
        request = folder / "request.json"
        request.write_text(json.dumps({"provider": "lean", "operation": "backtest", "leanRoot": str(runtime), "input": data}))
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        result = subprocess.run([python, str(bridge), str(request)], cwd=folder, capture_output=True, text=True, timeout=180, env=environment)
        if bool(result.returncode) != failure:
            print(result.stdout, result.stderr)
            if (folder / "lean.log").exists():
                print((folder / "lean.log").read_text(errors="replace")[-10000:])
            raise AssertionError(f"Unexpected LEAN worker exit {result.returncode}")
        if failure:
            return
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        assert any("result" in event for event in events), "Missing verified completion report"
        if minute:
            assert next(event["timestamp"] for event in events if event.get("event") == "asset") == "2024-01-02T14:31:00Z"
        else:
            assert any(event.get("event") == "fill" for event in events), "No real fills"
            assert float(next(event["result"]["finalEquity"] for event in events if "result" in event)) > 100000
        return events

execute(prefix + '    def on_data(self, data):\n        if not self.portfolio.invested: self.set_holdings("SPY", 0.5)\n')
execute(prefix.replace("Resolution.DAILY", "Resolution.MINUTE") + '    def on_data(self, data):\n        pass\n', minute=True)
execute(prefix + '    def on_data(self, data):\n        raise RuntimeError("intentional smoke failure")\n', failure=True)
execute(prefix.replace('        self.add_equity', '        self.set_account_currency("EUR")\n        self.add_equity'), failure=True)
print(f"PASS {manifest['target']}: relocated real backtest, fills/equity, minute timestamps, strategy failure")
