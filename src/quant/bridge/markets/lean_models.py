"""LEAN bindings for conservative next-observed-bar-close cash-equity execution."""
from AlgorithmImports import *
from datetime import timezone
import json
from pathlib import Path
from . import order_quantity_error, execution_price, fee_amount


class CashEquityBrokerageModel(DefaultBrokerageModel):
    def __init__(self):
        super().__init__(AccountType.CASH)

    def can_submit_order(self, security, order, message):
        # LEAN converts market intents placed at a session boundary into these
        # proxies. The host rejects explicit auction helper calls.
        if order.type not in (OrderType.MARKET, OrderType.MARKET_ON_OPEN, OrderType.MARKET_ON_CLOSE):
            return False, BrokerageMessageEvent(BrokerageMessageType.WARNING, "UnsupportedOrder", f"CN/HK adapter accepts market intents only; received {order.type}")
        return True, None

    def can_update_order(self, security, order, request, message):
        return False, BrokerageMessageEvent(BrokerageMessageType.WARNING, "UnsupportedUpdate", "Cancel the original order before submitting a replacement")


class CashEquityFillModel(FillModel):
    def __init__(self, algorithm, settings):
        super().__init__()
        self.algorithm, self.settings = algorithm, settings

    def market_on_open_fill(self, asset, order):
        return self.market_fill(asset, order)

    def market_on_close_fill(self, asset, order):
        return self.market_fill(asset, order)

    def market_fill(self, asset, order):
        utc = Extensions.convert_to_utc(asset.local_time, asset.exchange.time_zone)
        fill = OrderEvent(order, utc, OrderFee.ZERO)
        if order.status == OrderStatus.CANCELED:
            return fill
        bar = asset.cache.get_data(TradeBar)
        if bar is None or bar.is_fill_forward:
            return fill
        end = Extensions.convert_to_utc(bar.end_time, asset.exchange.time_zone)
        # Signals using the current close cannot trade at that same close.
        if end <= order.time:
            return fill
        symbol = asset.symbol.value
        state = self.algorithm._market_bar(symbol, end)
        if not state or state["tradingStatus"] != "normal" or bar.volume <= 0:
            return fill
        market = self.settings["market"]
        quantity = float(order.quantity)
        today = asset.local_time.date().isoformat()
        bought_today = self.algorithm._market_buys.get((symbol, today), 0)
        reason = order_quantity_error(market["profile"], market["securities"][symbol], quantity, float(asset.holdings.quantity), bought_today)
        if reason:
            fill.status, fill.message = OrderStatus.INVALID, reason
            return fill
        # All orders for a symbol share the observed bar's liquidity budget.
        previous_end, used = self.algorithm._market_filled_volume.get(symbol, (None, 0))
        if previous_end != end:
            used = 0
        if used + abs(quantity) > float(bar.volume):
            return fill
        buy = quantity > 0
        price = execution_price(market["securities"][symbol], float(bar.close), self.settings["slippage"], buy)
        if price < float(bar.low) or price > float(bar.high):
            return fill
        if (buy and state.get("limitUp") is not None and price >= state["limitUp"]) or (not buy and state.get("limitDown") is not None and price <= state["limitDown"]):
            return fill
        fee = fee_amount(market["rules"], abs(quantity)*price, buy, self.settings["commission"])
        if buy and abs(quantity)*price + fee > float(self.algorithm.portfolio.cash):
            fill.status, fill.message = OrderStatus.INVALID, "Insufficient cash including modeled fees"
            return fill
        fill.status, fill.fill_quantity, fill.fill_price = OrderStatus.FILLED, order.quantity, price
        fill.order_fee = OrderFee(CashAmount(fee, market["profile"]["currency"]))
        self.algorithm._market_filled_volume[symbol] = (end, used + abs(quantity))
        return fill
