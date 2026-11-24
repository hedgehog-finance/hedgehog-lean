"""Copied into each LEAN job. User strategy stays separate and immutable."""
from AlgorithmImports import *
import json
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from candidate import GatewayStrategy
from markets.lean_models import CashEquityBrokerageModel, CashEquityFillModel
from markets import fee_amount

ROOT = Path(__file__).parent
SETTINGS = json.loads((ROOT / "settings.json").read_text())


class GatewayFeeModel(FeeModel):
    def get_order_fee(self, parameters):
        notional = float(abs(parameters.order.quantity) * parameters.security.price)
        rules = SETTINGS["market"]["rules"]
        amount = fee_amount(rules, notional, parameters.order.quantity > 0, SETTINGS["commission"]) if rules else notional * SETTINGS["commission"]
        return OrderFee(CashAmount(amount, SETTINGS["market"]["profile"]["currency"]))


class GatewayHostAlgorithm(GatewayStrategy):
    def _configure_security(self, security):
        symbol = security.symbol
        market = SETTINGS["market"]
        if symbol.value not in SETTINGS["symbols"] or symbol.security_type != SecurityType.EQUITY or symbol.id.market != market["securities"][symbol.value]["exchange"]:
            raise ValueError("Strategy subscribed to a symbol outside the snapshot")
        expected = Resolution.DAILY if SETTINGS["frequency"] == "daily" else Resolution.MINUTE
        if security.resolution != expected:
            raise ValueError("Strategy frequency must match the snapshot")
        security.set_data_normalization_mode(DataNormalizationMode.RAW)
        security.set_fee_model(GatewayFeeModel())
        security.set_slippage_model(ConstantSlippageModel(SETTINGS["slippage"]))
        if market["rules"]:
            security.set_fill_model(CashEquityFillModel(self, SETTINGS))
            security.set_leverage(1)

    def on_securities_changed(self, changes):
        if SETTINGS["market"]["rules"]:
            for security in changes.added_securities:
                self._configure_security(security)
        super().on_securities_changed(changes)

    def market_on_open_order(self, *args, **kwargs):
        if SETTINGS["market"]["rules"]:
            raise ValueError("Explicit auction orders are not supported by the CN/HK bar model")
        return super().market_on_open_order(*args, **kwargs)

    def market_on_close_order(self, *args, **kwargs):
        if SETTINGS["market"]["rules"]:
            raise ValueError("Explicit auction orders are not supported by the CN/HK bar model")
        return super().market_on_close_order(*args, **kwargs)

    def add_equity(self, ticker, *args, **kwargs):
        market = SETTINGS["market"]
        if market["rules"]:
            security = market["securities"].get(ticker)
            if not security or len(args) > 1 or kwargs.get("market") not in (None, security["exchange"]):
                raise ValueError("Use a snapshot symbol and its declared market; pass advanced subscription settings by name")
            kwargs["market"], kwargs["fill_forward"] = security["exchange"], False
        security = super().add_equity(ticker, *args, **kwargs)
        if market["rules"]:
            self._configure_security(security)
        return security

    def initialize(self):
        market = SETTINGS["market"]
        profile = market["profile"]
        self._market_buys, self._market_states, self._market_offset = {}, {}, 0
        if market["rules"]:
            for exchange, identifier in profile["exchanges"].items():
                if Market.encode(exchange) is None:
                    Market.add(exchange, identifier)
            self.set_time_zone(profile["timezone"])
            self.set_account_currency(profile["currency"])
            self.set_brokerage_model(CashEquityBrokerageModel())
        super().initialize()
        if market["rules"]:
            if self.account_currency != profile["currency"]:
                raise ValueError("Strategy account currency must match its market snapshot")
            self.set_brokerage_model(CashEquityBrokerageModel())
        self.set_cash(SETTINGS["cash"])
        self.settings.daily_precise_end_time = True
        self._bar_ends = {symbol: set(ends) for symbol, ends in SETTINGS["barEnds"].items()}
        if not SETTINGS["paper"]:
            self.set_start_date(datetime.fromisoformat(SETTINGS["start"]))
            self.set_end_date(datetime.fromisoformat(SETTINGS["end"]))
        for symbol, security in self.securities.items():
            self._configure_security(security)
        if market["rules"]:
            self.set_benchmark(next(iter(self.securities.keys())))
        self._peak = SETTINGS["cash"]
        self._drawdown = 0.0

    def _market_bar(self, symbol, end):
        stamp = int(end.replace(tzinfo=timezone.utc).timestamp())
        with (ROOT / "market-events.jsonl").open() as stream:
            stream.seek(self._market_offset)
            while True:
                line = stream.readline()
                if not line or not line.endswith("\n"):
                    break
                bar = json.loads(line)
                at = int(datetime.fromisoformat(bar["time"].replace("Z", "+00:00")).timestamp())
                self._market_states[(bar["symbol"], at)] = bar
                self._market_offset = stream.tell()
        if SETTINGS["paper"]:
            self._market_states = {key: value for key, value in self._market_states.items() if key[1] >= stamp - 86400}
        return self._market_states.get((symbol, stamp))

    def _event(self, kind, data):
        with (ROOT / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"type": kind, "time": self.utc_time.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"), "data": data}) + "\n")

    def on_data(self, data):
        if not SETTINGS["paper"]:
            for symbol, bar in data.bars.items():
                if not bar.is_fill_forward:
                    end = int(bar.end_time.replace(tzinfo=ZoneInfo(SETTINGS["market"]["profile"]["timezone"])).timestamp())
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
        if event.fill_quantity > 0 and SETTINGS["market"]["rules"]:
            day = self.utc_time.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(SETTINGS["market"]["profile"]["timezone"])).date().isoformat()
            key = (event.symbol.value, day)
            self._market_buys[key] = self._market_buys.get(key, 0) + float(event.fill_quantity)
        super().on_order_event(event)
        self._event("order", {"orderId": event.order_id, "symbol": event.symbol.value, "status": str(event.status), "message": event.message})
        if event.fill_quantity:
            self._event("fill", {"orderId": event.order_id, "symbol": event.symbol.value, "quantity": str(event.fill_quantity), "price": str(event.fill_price), "fee": str(event.order_fee.value.amount)})

    def on_end_of_algorithm(self):
        super().on_end_of_algorithm()
        (ROOT / "summary.json").write_text(json.dumps({"initialCash": SETTINGS["cash"], "finalEquity": float(self.portfolio.total_portfolio_value),
            "return": float(self.portfolio.total_portfolio_value) / SETTINGS["cash"] - 1, "maxDrawdown": self._drawdown,
            "marketProfile": SETTINGS["market"]["profile"]["id"], "marketAdapterVersion": SETTINGS["marketAdapterVersion"],
            "marketRulesVersion": (SETTINGS["market"]["rules"] or {}).get("version"), "currency": SETTINGS["market"]["profile"]["currency"],
            "assumptions": (["Versioned user calendar, fees and security rules", "Next observed bar close; market intents only; no auction/order-book modeling", "Whole-share cash positions; no short selling", "Trade-date cash accounting, not withdrawal settlement", "Raw prices; corporate-action-free window required"] if SETTINGS["market"]["rules"] else ["USA equity calendar", "Raw prices; no corporate-action adjustment", "LEAN default equity fill model"])}))
