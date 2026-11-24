"""Copied into each LEAN job. User strategy stays separate and immutable."""
from AlgorithmImports import *
import json
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from candidate import GatewayStrategy

ROOT = Path(__file__).parent
SETTINGS = json.loads((ROOT / "settings.json").read_text())


class GatewayFeeModel(FeeModel):
    def get_order_fee(self, parameters):
        amount = abs(parameters.order.quantity) * parameters.security.price * SETTINGS["commission"]
        return OrderFee(CashAmount(amount, "USD"))


class GatewayHostAlgorithm(GatewayStrategy):
    def initialize(self):
        super().initialize()
        self.set_cash(SETTINGS["cash"])
        self.settings.daily_precise_end_time = True
        self._bar_ends = {symbol: set(ends) for symbol, ends in SETTINGS["barEnds"].items()}
        if not SETTINGS["paper"]:
            self.set_start_date(datetime.fromisoformat(SETTINGS["start"]))
            self.set_end_date(datetime.fromisoformat(SETTINGS["end"]))
        for symbol, security in self.securities.items():
            if symbol.value not in SETTINGS["symbols"] or symbol.security_type != SecurityType.EQUITY or symbol.id.market != Market.USA:
                raise ValueError("Strategy subscribed to a symbol outside the snapshot")
            expected = Resolution.DAILY if SETTINGS["frequency"] == "daily" else Resolution.MINUTE
            if security.resolution != expected:
                raise ValueError("Strategy frequency must match the snapshot")
            security.set_data_normalization_mode(DataNormalizationMode.RAW)
            security.set_fee_model(GatewayFeeModel())
            security.set_slippage_model(ConstantSlippageModel(SETTINGS["slippage"]))
        self._peak = SETTINGS["cash"]
        self._drawdown = 0.0

    def _event(self, kind, data):
        with (ROOT / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"type": kind, "time": self.utc_time.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"), "data": data}) + "\n")

    def on_data(self, data):
        if not SETTINGS["paper"]:
            for symbol, bar in data.bars.items():
                if not bar.is_fill_forward:
                    end = int(bar.end_time.replace(tzinfo=ZoneInfo("America/New_York")).timestamp())
                    if end not in self._bar_ends.get(symbol.value, set()):
                        raise ValueError("Bar end does not match the snapshot/exchange calendar; refusing to shift availability")
        super().on_data(data)
        equity = float(self.portfolio.total_portfolio_value)
        self._peak = max(self._peak, equity)
        self._drawdown = max(self._drawdown, 1 - equity / self._peak)
        self._event("asset", {"equity": str(self.portfolio.total_portfolio_value), "cash": str(self.portfolio.cash)})
        for symbol, holding in self.portfolio.items():
            self._event("position", {"symbol": symbol.value, "quantity": str(holding.quantity), "averagePrice": str(holding.average_price)})

    def on_order_event(self, event):
        super().on_order_event(event)
        self._event("order", {"orderId": event.order_id, "symbol": event.symbol.value, "status": str(event.status)})
        if event.fill_quantity:
            self._event("fill", {"orderId": event.order_id, "symbol": event.symbol.value, "quantity": str(event.fill_quantity), "price": str(event.fill_price), "fee": str(event.order_fee.value.amount)})

    def on_end_of_algorithm(self):
        super().on_end_of_algorithm()
        (ROOT / "summary.json").write_text(json.dumps({"initialCash": SETTINGS["cash"], "finalEquity": float(self.portfolio.total_portfolio_value),
            "return": float(self.portfolio.total_portfolio_value) / SETTINGS["cash"] - 1, "maxDrawdown": self._drawdown,
            "assumptions": ["USA equity calendar", "Raw prices; no corporate-action adjustment", "LEAN default equity fill model"]}))
