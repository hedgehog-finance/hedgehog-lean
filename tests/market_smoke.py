"""Native LEAN acceptance for CN/HK cash-equity adapters; synthetic fixture data only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

runtime = Path(sys.argv[1]).resolve()
bridge = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else runtime / "bridge"
manifest = json.loads((runtime / "runtime.json").read_text())
python = str(runtime / manifest["python"])


def fixture(profile, frequency="daily"):
    cn = profile == "cn-equity"
    symbol, currency = ("600519", "CNY") if cn else ("00005", "HKD")
    rules = {"profile": profile, "version": "fixture-2024-v1", "validFrom": "2024-01-01", "validUntil": "2024-01-31", "corporateActions": "none-in-window", "cashAccounting": "trade-date",
             "calendar": {"version": "fixture-calendar-v1", "sessions": [{"start": "09:30", "end": "11:30" if cn else "12:00"}, {"start": "13:00", "end": "15:00" if cn else "16:00"}], "holidays": ["2024-01-01"]},
             "securities": [{"symbol": symbol, "kind": "stock", "exchange": "sse" if cn else "hkex", "lotSize": 100, "minBuyQuantity": 100, "quantityStep": 100, "tickBands": [{"upTo": 1000, "tick": 0.01}]}],
             "feeVersion": "synthetic-fees-v1", "fees": [{"name": "fixture-sell-tax", "side": "sell", "rate": 0.0005, "minimum": 0, "roundingUnit": 0.01}]}
    times = [f"2024-01-{day:02}T{'07' if cn else '08'}:00:00Z" for day in (2,3,4,5,8,9)] if frequency == "daily" else [f"2024-01-{day:02}T01:{minute}:00Z" for day,minute in ((2,31),(2,32),(2,33),(2,34),(3,31),(3,32))]
    bars = [{"symbol":symbol,"time":at,"availableAt":at,"open":10+i/10,"high":10.5+i/10,"low":9.5+i/10,"close":10.1+i/10,"volume":100000,"tradingStatus":"normal",**({"limitUp":15,"limitDown":5} if cn else {})} for i,at in enumerate(times)]
    prefix = f'from AlgorithmImports import *\nclass GatewayStrategy(QCAlgorithm):\n    def initialize(self):\n        self.add_equity("{symbol}", Resolution.{frequency.upper()})\n        self.n = 0\n'
    code = prefix + f'    def on_data(self, data):\n        self.n += 1\n        if self.n == 1: self.market_order("{symbol}", 100)\n        if self.n in (2,5) and self.portfolio.invested: self.market_order("{symbol}", -100)\n'
    return {"code":code,"bars":bars,"dataset":{"market":profile,"frequency":frequency,"symbols_json":json.dumps([symbol]),"adjustment_json":json.dumps({"prices":"raw","volumeUnit":"shares","currency":currency,"marketRules":rules})},"initialCash":100000,"commission":0.001,"slippage":0}


def execute(data, failure=False):
    with tempfile.TemporaryDirectory(prefix="lean-market-test-") as directory:
        folder=Path(directory); request=folder/"input.json"
        request.write_text(json.dumps({"provider":"lean","operation":"backtest","leanRoot":str(runtime),"input":data}))
        result=subprocess.run([python,str(bridge/"worker.py"),str(request)],cwd=folder,capture_output=True,text=True,timeout=150)
        if bool(result.returncode) != failure:
            print(result.stdout, result.stderr)
            if (folder/"lean.log").exists(): print((folder/"lean.log").read_text(errors="replace")[-12000:])
            raise AssertionError(f"Unexpected LEAN worker exit {result.returncode}")
        if failure:return []
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        if not any(event.get("event") == "fill" for event in events):
            print(result.stdout)
            if (folder/"lean.log").exists(): print((folder/"lean.log").read_text(errors="replace")[-7000:])
        return events


if __name__ == "__main__":
    for profile in ("cn-equity","hk-equity"):
        daily=execute(fixture(profile))
        summary=next(event["result"] for event in daily if "result" in event)
        assert summary["marketProfile"]==profile and summary["marketRulesVersion"]=="fixture-2024-v1"
        assert any(event.get("event")=="fill" for event in daily)
        minute=execute(fixture(profile,"minute"))
        fills=[event for event in minute if event.get("event")=="fill"]
        assert fills[0]["timestamp"]=="2024-01-02T01:32:00Z", fills
        if profile=="cn-equity":
            assert any(event.get("event")=="order" and "sellable" in event["data"].get("message","") for event in minute)
            assert [event for event in fills if float(event["data"]["quantity"])<0][0]["timestamp"]=="2024-01-03T01:32:00Z"
        else:
            assert [event for event in fills if float(event["data"]["quantity"])<0][0]["timestamp"]=="2024-01-02T01:33:00Z"
        assert all(event["data"]["currency"]==("CNY" if profile=="cn-equity" else "HKD") for event in fills)
        blocked=fixture(profile)
        blocked["bars"][1]["tradingStatus"]="restricted"
        blocked["bars"][2]["tradingStatus"]="halted"
        blocked["bars"][2]["volume"]=0
        blocked_events=execute(blocked)
        first_fill=next(event for event in blocked_events if event.get("event")=="fill")
        assert first_fill["timestamp"].startswith("2024-01-05"), first_fill
        if profile=="cn-equity":
            locked=fixture(profile)
            locked["bars"][1]["limitUp"]=locked["bars"][1]["close"]
            locked["bars"][1]["high"]=locked["bars"][1]["close"]
            locked_events=execute(locked)
            assert next(event for event in locked_events if event.get("event")=="fill")["timestamp"].startswith("2024-01-04")
        bad=fixture(profile);bad["bars"][0].pop("tradingStatus");execute(bad,failure=True)
        print(f"PASS {profile}: native daily/minute execution, next-bar fills, sellability, currency, halts/restrictions/limits, missing-rule rejection")
