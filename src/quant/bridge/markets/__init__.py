"""Versioned, data-driven cash-equity profiles shared by backtests and paper runs."""
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .us_equity import PROFILE as US
from .cn_equity import PROFILE as CN
from .hk_equity import PROFILE as HK

PROFILES = {profile["id"]: profile for profile in (US, CN, HK)}
VERSION = "1.0.1"


def profile_for(market):
    for profile in PROFILES.values():
        if market.lower() in profile["aliases"]:
            return profile
    raise ValueError("Unsupported LEAN equity market")


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def local_time(value, profile):
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("Market timestamps require a timezone")
    return at.astimezone(ZoneInfo(profile["timezone"]))


def load_market(dataset, bars):
    profile = profile_for(dataset["market"])
    adjustment = json.loads(dataset["adjustment_json"])
    if adjustment.get("prices") != "raw" or adjustment.get("volumeUnit") != "shares" or adjustment.get("currency") != profile["currency"]:
        raise ValueError(f'{profile["id"]} requires raw prices, share volume and {profile["currency"]}')
    symbols = json.loads(dataset["symbols_json"])
    rules = adjustment.get("marketRules")
    if profile["id"] == "us-equity":
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,15}", symbol) for symbol in symbols):
            raise ValueError("Unsupported US equity symbol")
        return {"profile": profile, "rules": None, "securities": {symbol: {"exchange": "usa"} for symbol in symbols}}
    if not rules or rules.get("profile") != profile["id"] or not rules.get("version"):
        raise ValueError("A versioned marketRules snapshot is required for CN/HK equities")
    if rules.get("corporateActions") != "none-in-window" or rules.get("cashAccounting") != "trade-date":
        raise ValueError("This model requires an explicitly corporate-action-free window and trade-date cash accounting")
    start, end = date.fromisoformat(rules["validFrom"]), date.fromisoformat(rules["validUntil"])
    if start > end or (end-start).days > 3660:
        raise ValueError("Invalid market rule coverage")
    calendar = rules["calendar"]
    if not calendar.get("version") or not calendar.get("sessions"):
        raise ValueError("A versioned exchange calendar is required")
    previous = "00:00"
    for session in calendar["sessions"]:
        if not all(re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", session[key]) for key in ("start", "end")) or not previous <= session["start"] < session["end"]:
            raise ValueError("Invalid or overlapping exchange sessions")
        previous = session["end"]
    for holiday in calendar["holidays"]:
        date.fromisoformat(holiday)
    for day, close in calendar.get("earlyCloses", {}).items():
        date.fromisoformat(day)
        if not any(session["start"] < close <= session["end"] for session in calendar["sessions"]):
            raise ValueError("Early close must fall within an exchange session")
    securities = {}
    for security in rules["securities"]:
        symbol = security["symbol"]
        if security.get("kind") != "stock":
            raise ValueError("This profile supports ordinary cash equities only")
        pattern = r"\d{6}" if profile["id"] == "cn-equity" else r"\d{5}"
        if not re.fullmatch(pattern, symbol) or symbol in securities or security["exchange"] not in profile["exchanges"]:
            raise ValueError("Invalid or duplicate equity symbol/exchange")
        for key in ("lotSize", "minBuyQuantity", "quantityStep"):
            if not isinstance(security[key], int) or isinstance(security[key], bool) or security[key] <= 0:
                raise ValueError("Trading quantities must be positive integers")
        if profile["id"] == "hk-equity" and (security["minBuyQuantity"] != security["lotSize"] or security["quantityStep"] != security["lotSize"]):
            raise ValueError("HK model supports board-lot orders only")
        prior = 0
        for band in security["tickBands"]:
            if not positive(band["upTo"]) or band["upTo"] <= prior or not positive(band["tick"]):
                raise ValueError("Invalid security tick bands")
            prior = band["upTo"]
        if not prior:
            raise ValueError("Security tick bands are required")
        securities[symbol] = security
    if set(securities) != set(symbols):
        raise ValueError("Market rules must cover exactly the dataset universe")
    if not rules.get("feeVersion"):
        raise ValueError("Historical fee version is required")
    for fee in rules["fees"]:
        if fee["side"] not in ("buy", "sell", "both") or not positive(fee["roundingUnit"]) or any(not isinstance(fee[key], (int,float)) or not math.isfinite(fee[key]) or fee[key] < 0 for key in ("rate","minimum")):
            raise ValueError("Invalid market fee component")
    market = {"profile": profile, "rules": rules, "securities": securities}
    for bar in bars:
        validate_bar(market, bar, dataset["frequency"])
    return market


def validate_bar(market, bar, frequency):
    profile, rules = market["profile"], market["rules"]
    at = local_time(bar["time"], profile)
    if local_time(bar["availableAt"], profile) != at or at.second or at.microsecond:
        raise ValueError("LEAN requires minute-aligned bar end = availableAt")
    if not rules:
        return
    day, clock = at.date().isoformat(), at.strftime("%H:%M")
    calendar = rules["calendar"]
    if not rules["validFrom"] <= day <= rules["validUntil"] or at.weekday() >= 5 or day in calendar["holidays"]:
        raise ValueError("Bar falls outside the market rule coverage or trading calendar")
    close = calendar.get("earlyCloses", {}).get(day, calendar["sessions"][-1]["end"])
    if (frequency == "daily" and clock != close) or (frequency == "minute" and not any(session["start"] < clock <= min(session["end"], close) for session in calendar["sessions"])):
        raise ValueError("Bar end does not match the exchange session/close")
    if bar.get("tradingStatus") not in ("normal", "halted", "restricted"):
        raise ValueError("CN/HK bars require explicit tradingStatus")
    if profile["id"] == "cn-equity":
        if "limitUp" not in bar or "limitDown" not in bar or (bar["limitUp"] is None) != (bar["limitDown"] is None):
            raise ValueError("CN bars require both price limits, or explicit nulls for an unrestricted day")
    for key in ("limitUp", "limitDown"):
        if bar.get(key) is not None and not positive(bar[key]):
            raise ValueError("Invalid daily price limit")
    if bar.get("limitUp") is not None and bar.get("limitDown") is not None and (bar["limitDown"] > bar["limitUp"] or bar["high"] > bar["limitUp"] + 1e-8 or bar["low"] < bar["limitDown"] - 1e-8):
        raise ValueError("OHLC violates its declared price limits")
    security = market["securities"].get(bar["symbol"])
    if not security or max(bar["high"], bar["open"]) > security["tickBands"][-1]["upTo"]:
        raise ValueError("Market rules/tick bands do not cover this security price")


def write_databases(data_root, market):
    rules = market["rules"]
    if not rules:
        return
    profile, calendar = market["profile"], rules["calendar"]
    path = data_root / "market-hours/market-hours-database.json"
    database = json.loads(path.read_text())
    entry = {"dataTimeZone": profile["timezone"], "exchangeTimeZone": profile["timezone"], "holidays": [date.fromisoformat(day).strftime("%m/%d/%Y") for day in calendar["holidays"]], "earlyCloses": {date.fromisoformat(day).strftime("%m/%d/%Y"): value+":00" for day,value in calendar.get("earlyCloses",{}).items()}}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        entry[day] = [{"start": s["start"]+":00", "end": s["end"]+":00", "state": "market"} for s in calendar["sessions"]]
    entry["saturday"], entry["sunday"] = [], []
    for exchange in profile["exchanges"]:
        database["entries"][f"Equity-{exchange}-[*]"] = entry
    path.write_text(json.dumps(database))
    with (data_root / "symbol-properties/symbol-properties-database.csv").open("a", newline="") as stream:
        writer = csv.writer(stream)
        for symbol, security in market["securities"].items():
            writer.writerow([security["exchange"], symbol, "equity", symbol, profile["currency"], 1, security["tickBands"][0]["tick"], security["quantityStep"], symbol, security["minBuyQuantity"], 1, 1])


def order_quantity_error(profile, security, quantity, holding, bought_today):
    if quantity != int(quantity) or not quantity:
        return "Whole-share nonzero orders are required"
    if quantity < 0:
        available = holding - (bought_today if profile["sellDelay"] else 0)
        if -quantity > available:
            return "Insufficient sellable shares (short sales and unsettled same-day CN purchases are unavailable)"
        if profile["id"] == "cn-equity" and -quantity == holding:
            return None
    absolute = abs(quantity)
    if absolute < security["minBuyQuantity"] or (absolute-security["minBuyQuantity"]) % security["quantityStep"]:
        return "Order does not satisfy this security's minimum quantity/increment"
    return None


def execution_price(security, close, slippage, buy):
    value = Decimal(str(close)) * (Decimal(1) + Decimal(str(slippage)) * (1 if buy else -1))
    for _ in range(3):
        tick = next((Decimal(str(b["tick"])) for b in security["tickBands"] if value <= Decimal(str(b["upTo"]))), None)
        if tick is None:
            raise ValueError("Execution price is outside declared tick bands")
        rounded = (value / tick).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR) * tick
        if rounded == value:
            break
        value = rounded
    return float(value)


def fee_amount(rules, notional, buy, commission):
    value = Decimal(str(abs(notional)))
    total = value * Decimal(str(commission))
    for fee in rules["fees"]:
        if fee["side"] not in ("both", "buy" if buy else "sell"):
            continue
        amount = max(value * Decimal(str(fee["rate"])), Decimal(str(fee["minimum"])))
        unit = Decimal(str(fee["roundingUnit"]))
        total += (amount / unit).to_integral_value(rounding=ROUND_CEILING) * unit
    return float(total)
