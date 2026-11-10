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

    def test_tp1_then_breakeven_then_stop_gives_managed_half_r(self):
        state={"trade_journal":[self.make_trade()]}
        events=journal.update_from_candles(state,"BTCUSD",[candle(2000,111,101),candle(3000,105,99),candle(4000,100,89)])
        trade=state["trade_journal"][0]
        self.assertEqual([e["event"] for e in events],["tp1","be","sl"])
        self.assertEqual(trade["managed_r"],0.5)
        self.assertEqual(journal.base_r(trade),-1.0)

    def test_tp2_without_return_to_entry_gives_managed_one_and_half(self):
        state={"trade_journal":[self.make_trade()]}
        events=journal.update_from_candles(state,"BTCUSD",[candle(2000,111,101),candle(3000,121,102)])
        self.assertEqual([e["event"] for e in events],["tp1","tp2"])
        self.assertEqual(state["trade_journal"][0]["managed_r"],1.5)

    def test_stop_with_tp1_already_hit_earlier_is_not_ambiguous(self):
        state={"trade_journal":[self.make_trade()]}
        journal.update_from_candles(state,"BTCUSD",[candle(2000,111,101),candle(3000,111,89)])
        self.assertEqual(state["trade_journal"][0]["status"],"sl")

    def test_events_are_not_repeated_on_next_cycle(self):
        state={"trade_journal":[self.make_trade()]}
        candles=[candle(2000,111,101)]
        self.assertEqual(len(journal.update_from_candles(state,"BTCUSD",candles)),1)
        self.assertEqual(journal.update_from_candles(state,"BTCUSD",candles),[])

    def test_short_trade_breakeven(self):
        t=self.make_trade(); t.update({"direction":"short","sl":110.0,"tp1":90.0,"tp2":80.0})
        state={"trade_journal":[t]}
        events=journal.update_from_candles(state,"BTCUSD",[candle(2000,99,89),candle(3000,101,95)])
        self.assertEqual([e["event"] for e in events],["tp1","be"])

    def test_legacy_managed_r(self):
        a=self.make_trade(); a.update({"status":"sl","tp1_hit":True})
        b=self.make_trade(); b.update({"status":"sl"})
        c=self.make_trade(); c.update({"status":"tp2","tp1_hit":True})
        state={"trade_journal":[a,b,c]}
        journal.migrate_legacy_metrics(state)
        self.assertEqual([a["managed_r"],b["managed_r"],c["managed_r"]],[0.5,-1.0,1.5])
        self.assertTrue(c.get("managed_estimated"))

    def test_stats_and_report(self):
        import notifier
        a=self.make_trade(); a.update({"status":"sl","tp1_hit":True,"managed_r":0.5,"signal_key":"pullback_long"})
        b=self.make_trade(); b.update({"status":"tp2","tp1_hit":True,"managed_r":1.5,"signal_key":"pullback_long"})
        c=self.make_trade(); c.update({"status":"sl","managed_r":-1.0,"signal_key":"breakout_long","shadow":True})
        st=journal.stats([a,b])
        self.assertEqual((st["closed"],st["base_r"],st["managed_r"]),(2,1.0,2.0))
        text=notifier.format_report([a,b,c])
        self.assertIn("pullback_long",text)
        self.assertIn("Filtrati",text)
        self.assertIn("+2,0R",text)


class CommandTests(unittest.TestCase):
    class FakeBot:
        def __init__(self, commands): self.commands, self.sent = commands, []
        def poll_commands(self, offset): return self.commands, offset + 1
        def send(self, text): self.sent.append(text); return True

    def test_commands(self):
        import main
        bot=self.FakeBot(["/aiuto","/report 30","/stato","/oracolo","/boh"])
        req=main.handle_commands({}, {"trade_journal":[]}, bot)
        self.assertEqual(req,{"stato","oracolo"})
        self.assertEqual(len(bot.sent),3)
        self.assertIn("/report",bot.sent[0])
        self.assertIn("ultimi 30 giorni",bot.sent[1])

    def test_quiet_hours_queue_then_flush(self):
        import main
        bot=self.FakeBot([]); state={}
        main.queue_or_send(state, bot, "A", quiet=True)
        self.assertEqual(bot.sent,[])
        main.flush_pending(state, bot)
        self.assertEqual(len(bot.sent),1); self.assertIn("A",bot.sent[0])
        self.assertEqual(state["pending_notices"],[])


if __name__=="__main__":
    unittest.main()
