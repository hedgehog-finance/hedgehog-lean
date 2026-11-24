"""Market-rule acceptance tests, independent of LEAN and third-party services."""
import copy
from pathlib import Path
import sys
import unittest
for parent in Path(__file__).resolve().parents:
    bridge = parent / "src/quant/bridge"
    if bridge.is_dir():
        sys.path.insert(0, str(bridge))
        break
from markets import profile_for, load_market, validate_bar, order_quantity_error, execution_price, fee_amount
import json


def sample(profile="cn-equity"):
    cn = profile == "cn-equity"
    security = {"symbol": "600519" if cn else "00005", "kind": "stock", "exchange": "sse" if cn else "hkex", "lotSize": 100, "minBuyQuantity": 100, "quantityStep": 100, "tickBands": [{"upTo": 100, "tick": .01}, {"upTo": 1000, "tick": .1}]}
    rules = {"profile": profile, "version": "test-v1", "validFrom": "2024-01-01", "validUntil": "2024-01-31", "corporateActions": "none-in-window", "cashAccounting": "trade-date", "calendar": {"version": "test", "sessions": [{"start":"09:30","end":"11:30" if cn else "12:00"},{"start":"13:00","end":"15:00" if cn else "16:00"}],"holidays":["2024-01-01"]}, "securities":[security], "feeVersion":"test-fees", "fees":[]}
    dataset = {"market":profile,"frequency":"daily","symbols_json":json.dumps([security["symbol"]]),"adjustment_json":json.dumps({"prices":"raw","currency":"CNY" if cn else "HKD","volumeUnit":"shares","marketRules":rules})}
    bar = {"symbol":security["symbol"],"time":"2024-01-02T07:00:00Z" if cn else "2024-01-02T08:00:00Z","availableAt":"2024-01-02T07:00:00Z" if cn else "2024-01-02T08:00:00Z","open":10,"high":11,"low":9,"close":10,"volume":1000,"tradingStatus":"normal","limitUp":11,"limitDown":9}
    return dataset, bar


class MarketTests(unittest.TestCase):
    def test_explicit_profile_currency_and_rules(self):
        for profile in ("cn-equity","hk-equity"):
            dataset, bar = sample(profile)
            self.assertEqual(load_market(dataset,[bar])["profile"]["id"],profile)
            dataset["adjustment_json"] = json.dumps({"prices":"raw","currency":"USD","volumeUnit":"shares"})
            with self.assertRaises(ValueError): load_market(dataset,[bar])

    def test_calendar_holidays_coverage_and_lunch_break(self):
        dataset, bar = sample(); market=load_market(dataset,[bar])
        for at in ("2024-01-01T07:00:00Z","2024-02-01T07:00:00Z","2024-01-06T07:00:00Z","2024-01-02T04:00:00Z"):
            invalid=dict(bar,time=at,availableAt=at)
            with self.assertRaises(ValueError): validate_bar(market,invalid,"minute")

    def test_half_day_close_and_no_delayed_backdating(self):
        dataset, bar=sample("hk-equity"); market=load_market(dataset,[bar])
        market["rules"]["calendar"]["earlyCloses"]={"2024-01-02":"12:00"}
        with self.assertRaises(ValueError): validate_bar(market,bar,"daily")
        bar.update(time="2024-01-02T04:00:00Z",availableAt="2024-01-02T04:00:00Z")
        validate_bar(market,bar,"daily")
        bar["availableAt"]="2024-01-02T04:01:00Z"
        with self.assertRaises(ValueError):validate_bar(market,bar,"daily")

    def test_price_limits_must_be_explicit_and_consistent(self):
        dataset, bar=sample();market=load_market(dataset,[bar])
        invalid=copy.deepcopy(bar);del invalid["limitUp"]
        with self.assertRaises(ValueError):validate_bar(market,invalid,"daily")
        with self.assertRaises(ValueError):validate_bar(market,dict(bar,limitUp=10),"daily")
        validate_bar(market,dict(bar,limitUp=None,limitDown=None),"daily")

    def test_cn_locked_shares_and_residual_sale(self):
        profile=profile_for("cn");security={"minBuyQuantity":100,"quantityStep":100}
        self.assertIsNotNone(order_quantity_error(profile,security,-100,100,100))
        self.assertIsNone(order_quantity_error(profile,security,-100,100,0))
        self.assertIsNone(order_quantity_error(profile,security,-51,51,0))
        self.assertIsNotNone(order_quantity_error(profile,security,51,0,0))
        self.assertIsNotNone(order_quantity_error(profile,security,-101,100,0))
        self.assertIsNotNone(order_quantity_error(profile,security,100.5,0,0))

    def test_hk_same_day_and_board_lots(self):
        profile=profile_for("hk");security={"minBuyQuantity":500,"quantityStep":500}
        self.assertIsNone(order_quantity_error(profile,security,-500,500,500))
        self.assertIsNotNone(order_quantity_error(profile,security,100,0,0))
        self.assertIsNotNone(order_quantity_error(profile,security,-51,51,0))

    def test_tick_bands_and_directional_rounding(self):
        security={"tickBands":[{"upTo":100,"tick":.01},{"upTo":1000,"tick":.1}]}
        self.assertEqual(execution_price(security,100.01,0,True),100.1)
        self.assertEqual(execution_price(security,100.09,0,False),100.0)
        with self.assertRaises(ValueError):execution_price(security,1001,0,True)

    def test_fee_side_minimum_and_rounding(self):
        rules={"fees":[{"name":"test","side":"sell","rate":.001,"minimum":0,"roundingUnit":1}]}
        self.assertEqual(fee_amount(rules,1001,True,0),0)
        self.assertEqual(fee_amount(rules,1001,False,0),2)
        rules["fees"][0]["minimum"]=5
        self.assertEqual(fee_amount(rules,1001,False,0),5)

if __name__=="__main__": unittest.main()
