import unittest
from datetime import datetime, timedelta, timezone

import main
import notifier


class FakeBot:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
CFG = {"quiet_hours_utc": []}


class HealthTests(unittest.TestCase):
    def test_short_gap_is_silent(self):
        state = {"health": {"last_cycle_completed_at": (NOW - timedelta(minutes=8)).isoformat()}}
        bot = FakeBot()
        main.check_gap(CFG, state, bot, NOW)
        self.assertEqual(bot.sent, [])
        self.assertEqual(state["health"].get("gaps", []), [])

    def test_long_gap_is_recorded_and_alerted(self):
        state = {"health": {"last_cycle_completed_at": (NOW - timedelta(minutes=95)).isoformat()}}
        bot = FakeBot()
        main.check_gap(CFG, state, bot, NOW)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("1 ora e 35 minuti", bot.sent[0])
        self.assertEqual(state["health"]["gaps"][0]["minutes"], 95)

    def test_medium_gap_recorded_not_alerted(self):
        state = {"health": {"last_cycle_completed_at": (NOW - timedelta(minutes=25)).isoformat()}}
        bot = FakeBot()
        main.check_gap(CFG, state, bot, NOW)
        self.assertEqual(bot.sent, [])
        self.assertEqual(len(state["health"]["gaps"]), 1)

    def test_data_outage_alert_once_then_recovery(self):
        state, bot = {}, FakeBot()
        main.track_data_health(CFG, state, bot, "XAUUSD", False, "HTTP 500", NOW)
        main.track_data_health(CFG, state, bot, "XAUUSD", False, "HTTP 500", NOW + timedelta(minutes=10))
        self.assertEqual(bot.sent, [])
        main.track_data_health(CFG, state, bot, "XAUUSD", False, "HTTP 500", NOW + timedelta(minutes=31))
        main.track_data_health(CFG, state, bot, "XAUUSD", False, "HTTP 500", NOW + timedelta(minutes=40))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("XAUUSD", bot.sent[0])
        main.track_data_health(CFG, state, bot, "XAUUSD", True, "", NOW + timedelta(minutes=50))
        self.assertEqual(len(bot.sent), 2)
        self.assertIn("di nuovo regolari", bot.sent[1])

    def test_short_outage_never_alerts(self):
        state, bot = {}, FakeBot()
        main.track_data_health(CFG, state, bot, "BTCUSD", False, "x", NOW)
        main.track_data_health(CFG, state, bot, "BTCUSD", True, "", NOW + timedelta(minutes=10))
        self.assertEqual(bot.sent, [])

    def test_health_report(self):
        cycles = [{"at": (NOW - timedelta(minutes=5 * i)).isoformat(timespec="minutes"),
                   "trigger": "workflow_dispatch" if i % 4 else "schedule"} for i in range(100)]
        state = {"health": {"cycles": cycles, "last_cycle_completed_at": NOW.isoformat(),
                            "gaps": [{"from": "2026-11-13T10:00+00:00", "to": "2026-11-13T11:10+00:00", "minutes": 70}],
                            "symbols": {"BTCUSD": {"status": "ok", "data_age_minutes": 3.0}}}}
        text = notifier.format_health(state, NOW)
        self.assertIn("Giri eseguiti: 100", text)
        self.assertIn("di solito 5 min", text)
        self.assertIn("1 ora e 10 minuti", text)
        self.assertIn("avvio esterno/manuale 75", text)


if __name__ == "__main__":
    unittest.main()
