import unittest
from types import SimpleNamespace
import journal

def candle(ts, high, low):
    return SimpleNamespace(ts=ts, high=high, low=low)

class JournalTests(unittest.TestCase):
    def make_trade(self):
        return {"symbol":"BTCUSD","direction":"long","entry":100.0,"sl":90.0,
                "tp1":110.0,"tp2":120.0,"entry_ts":1000,"last_checked_ts":1000,
                "status":"open","tp1_hit":False,"tp2_hit":False,"mfe_units":0.0,
                "mae_units":0.0,"risk_per_unit":10.0}

    def test_tp1_then_stop_records_partial_target_and_r_excursions(self):
        state={"trade_journal":[self.make_trade()]}
        journal.update_from_candles(state,"BTCUSD",[candle(2000,112,99),candle(3000,101,89)])
        trade=state["trade_journal"][0]
        self.assertEqual(trade["status"],"sl")
        self.assertTrue(trade["tp1_hit"])
        self.assertEqual(trade["tp1_ts"],2000)
        self.assertGreaterEqual(trade["mfe_r"],1.2)
        self.assertGreaterEqual(trade["mae_r"],1.1)

    def test_same_candle_stop_and_target_is_ambiguous(self):
        state={"trade_journal":[self.make_trade()]}
        journal.update_from_candles(state,"BTCUSD",[candle(2000,111,89)])
        self.assertEqual(state["trade_journal"][0]["status"],"ambiguous_ohlc")

    def test_legacy_records_get_normalized_r_without_inventing_tp1_time(self):
        trade = self.make_trade()
        trade.update({"mfe_units": 20.0, "mae_units": 15.0, "status": "sl"})
        state = {"trade_journal": [trade]}
        self.assertEqual(journal.migrate_legacy_metrics(state), 1)
        self.assertEqual(trade["mfe_r"], 2.0)
        self.assertEqual(trade["mae_r"], 1.5)
        self.assertIsNone(trade["tp1_ts"])

    def test_out_of_order_candles_are_sorted_before_processing(self):
        state={"trade_journal":[self.make_trade()]}
        journal.update_from_candles(state,"BTCUSD",[candle(3000,101,89),candle(2000,112,99)])
        self.assertEqual(state["trade_journal"][0]["tp1_ts"],2000)
        self.assertEqual(state["trade_journal"][0]["status"],"sl")

if __name__=="__main__":
    unittest.main()
