"""Small explicit provider adapters. No installation, network model downloads or brokers."""
import ast
from datetime import datetime
import importlib.util
import json
import math
import sys
from pathlib import Path


def validate(code):
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return {"valid": False, "issues": [{"line": error.lineno, "message": error.msg}], "level": "syntax"}
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    issues = [] if "GatewayStrategy" in classes else [{"message": "Define class GatewayStrategy"}]
    return {"valid": not issues, "issues": issues, "level": "syntax-and-entrypoint", "sandbox": False}


def load_strategy(code, base):
    check = validate(code)
    if not check["valid"]:
        raise ValueError(json.dumps(check))
    path = Path.cwd() / "strategy.py"
    path.write_text(code)
    spec = importlib.util.spec_from_file_location("gateway_candidate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    strategy = module.GatewayStrategy
    if not issubclass(strategy, base):
        raise ValueError("Strategy inherits the wrong provider base class")
    return strategy


def execute(provider, operation, data):
    if operation == "validate" and provider in ("lean", "backtrader"):
        return validate(data["code"])
    if provider == "quantlib" and operation == "option-price":
        import QuantLib as ql
        date = lambda value: ql.DateParser.parseISO(value)
        if date(data["maturityDate"]) <= date(data["valuationDate"]):
            raise ValueError("Maturity must follow valuation")
        old_date = ql.Settings.instance().evaluationDate
        try:
            today = date(data["valuationDate"])
            ql.Settings.instance().evaluationDate = today
            dc = ql.Actual365Fixed()
            curve = lambda rate: ql.YieldTermStructureHandle(ql.FlatForward(today, rate, dc))
            vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), data["volatility"], dc))
            process = ql.BlackScholesMertonProcess(ql.QuoteHandle(ql.SimpleQuote(data["spot"])), curve(data["dividend"]), curve(data["rate"]), vol)
            payoff = ql.PlainVanillaPayoff(ql.Option.Call if data["optionType"] == "call" else ql.Option.Put, data["strike"])
            option = ql.VanillaOption(payoff, ql.EuropeanExercise(date(data["maturityDate"])))
            option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
            return {"npv": option.NPV(), "delta": option.delta(), "gamma": option.gamma(), "vega": option.vega(), "theta": option.theta(), "rho": option.rho(), "model": "Black-Scholes-Merton", "dayCount": "Actual/365 (Fixed)"}
        finally:
            ql.Settings.instance().evaluationDate = old_date
    raise ValueError("Unsupported immediate operation")


def backtrader(data, emit):
    import backtrader as bt
    import pandas as pd
    strategy = load_strategy(data["code"], bt.Strategy)
    adjustment = json.loads(data["dataset"]["adjustment_json"])
    if adjustment["prices"] != "raw" or adjustment["volumeUnit"] != "shares":
        raise ValueError("This adapter requires raw prices and volume in shares")
    class Audit(bt.Analyzer):
        def notify_order(self, order):
            timestamp = self.strategy.datetime.datetime().isoformat() + "+00:00"
            emit("order", {"orderId": order.ref, "symbol": order.data._name, "status": order.getstatusname(), "quantity": order.size}, timestamp)
            if order.status == order.Completed:
                emit("fill", {"orderId": order.ref, "symbol": order.data._name, "quantity": order.executed.size, "price": order.executed.price, "fee": order.executed.comm}, timestamp)
        def next(self):
            timestamp = self.strategy.datetime.datetime().isoformat() + "+00:00"
            emit("asset", {"equity": self.strategy.broker.getvalue(), "cash": self.strategy.broker.getcash()}, timestamp)
            for feed in self.strategy.datas:
                position = self.strategy.getposition(feed)
                emit("position", {"symbol": feed._name, "quantity": position.size, "averagePrice": position.price}, timestamp)
    engine = bt.Cerebro(stdstats=False)
    engine.broker.setcash(data["initialCash"])
    engine.broker.setcommission(commission=data["commission"])
    engine.broker.set_slippage_perc(data.get("slippage", 0))
    for symbol in sorted(set(bar["symbol"] for bar in data["bars"])):
        rows = [bar for bar in data["bars"] if bar["symbol"] == symbol]
        # Late publications become visible only at availableAt, never earlier.
        frame = pd.DataFrame(rows)
        frame.index = pd.to_datetime(frame["availableAt"], utc=True).dt.tz_convert(None)
        engine.adddata(bt.feeds.PandasData(dataname=frame, timeframe=bt.TimeFrame.Days if data["dataset"]["frequency"] == "daily" else bt.TimeFrame.Minutes), name=symbol)
    engine.addstrategy(strategy)
    engine.addanalyzer(Audit)
    engine.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    result = engine.run()[0]
    return {"initialCash": data["initialCash"], "finalEquity": engine.broker.getvalue(), "return": engine.broker.getvalue() / data["initialCash"] - 1,
            "maxDrawdownPercent": result.analyzers.drawdown.get_analysis().max.drawdown,
            "assumptions": ["Market orders fill at next available bar open", "No market-specific price limits, T+1 or lot-size enforcement", "No corporate-action adjustment"]}


def qlib(data, emit):
    import qlib
    from qlib.utils import init_instance_by_config
    task = data["task"]
    segments = task["dataset"]["kwargs"]["segments"]
    intervals = [(datetime.fromisoformat(str(segments[key][0])), datetime.fromisoformat(str(segments[key][1]))) for key in ("train", "valid", "test")]
    if any(start > end for start, end in intervals) or not (intervals[0][1] < intervals[1][0] and intervals[1][1] < intervals[2][0]):
        raise ValueError("Train, validation and test intervals must be ordered and disjoint")
    qlib.init(provider_uri=data["providerUri"], region=data["region"])
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])
    model.fit(dataset)
    prediction = model.predict(dataset)
    prediction.rename("score").reset_index().to_json("predictions.json", orient="records", date_format="iso")
    scores = json.loads(Path("predictions.json").read_text())
    emit("metric", {"testPredictions": len(scores)})
    return {"scores": scores[:10000], "total": len(scores), "truncated": len(scores) > 10000, "segments": segments, "artifactFiles": ["predictions.json"]}


def vnpy(data, emit):
    from vnpy_ctastrategy import CtaTemplate
    from vnpy_ctastrategy.backtesting import BacktestingEngine
    from vnpy.trader.constant import Interval
    engine = BacktestingEngine()
    engine.set_parameters(vt_symbol=data["vtSymbol"], interval=Interval(data["interval"]), start=datetime.fromisoformat(data["start"]),
                          end=datetime.fromisoformat(data["end"]), rate=data["rate"], slippage=data["slippage"], size=data["size"],
                          pricetick=data["pricetick"], capital=data["capital"])
    strategy = load_strategy(data["code"], CtaTemplate)
    engine.add_strategy(strategy, data["settings"])
    engine.load_data()
    if not engine.history_data:
        raise ValueError("No historical data in the user's vn.py database")
    engine.run_backtesting()
    engine.calculate_result()
    stats = engine.calculate_statistics(output=False)
    for trade in engine.get_all_trades():
        emit("fill", {"orderId": trade.orderid, "symbol": trade.vt_symbol, "side": trade.direction.value, "quantity": trade.volume, "price": trade.price}, trade.datetime.isoformat())
    return {"statistics": {key: value if isinstance(value, (str, int, float)) and not (isinstance(value, float) and not math.isfinite(value)) else str(value) for key, value in stats.items()}}


def forecast(data, model_path):
    import numpy as np
    import timesfm
    path = Path(model_path).resolve()
    if not path.exists():
        raise ValueError("Local TimesFM weights not found")
    model = timesfm.TimesFM_2p5_200M_torch(torch_compile=False)
    model.load_checkpoint(str(path), torch_compile=False)
    model.compile(timesfm.ForecastConfig(max_context=max(len(series) for series in data["series"]), max_horizon=data["horizon"], normalize_inputs=True, use_continuous_quantile_head=True))
    point, quantiles = model.forecast(horizon=data["horizon"], inputs=[np.array(series, dtype=float) for series in data["series"]])
    return {"point": point.tolist(), "quantiles": quantiles.tolist(), "model": "TimesFM-2.5-200M", "horizon": data["horizon"]}


def czsc(data):
    from czsc import CZSC, RawBar, Freq
    bars = [RawBar(symbol=data["symbol"], id=index, dt=datetime.fromisoformat(bar["time"].replace("Z", "+00:00")), freq=Freq(data["frequency"]),
                   open=bar["open"], close=bar["close"], high=bar["high"], low=bar["low"], vol=bar["volume"], amount=bar.get("amount", 0)) for index, bar in enumerate(data["bars"])]
    if len(bars) < 3 or any(a.dt >= b.dt for a, b in zip(bars, bars[1:])):
        raise ValueError("At least three chronological bars are required")
    result = CZSC(bars)
    return {"asOf": bars[-1].dt.isoformat(), "strokes": [{"start": bi.fx_a.dt.isoformat(), "end": bi.fx_b.dt.isoformat(), "direction": bi.direction.value,
                                                        "high": bi.high, "low": bi.low} for bi in result.bi_list],
            "limitations": ["Recent structures may change with later bars; this is not a point-in-time signal backtest"]}
