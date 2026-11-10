import math
import random
import unittest
from collections import namedtuple

import oracle

C = namedtuple("C", "ts open high low close volume")


def series(n=1300, seed=1):
    random.seed(seed)
    price, out = 70000.0, []
    t0 = 1_790_000_000_000 - (1_790_000_000_000 % oracle.BAR_MS)
    for i in range(n):
        o = price
        price *= math.exp(random.gauss(0, 0.002))
        hi, lo = max(o, price) * 1.001, min(o, price) * 0.999
        out.append(C(t0 + i * oracle.BAR_MS, o, hi, lo, price, abs(random.gauss(10, 3))))
    return out


class OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = oracle.load_model()

    def test_model_file_is_present_and_complete(self):
        self.assertIsNotNone(self.model)
        for label in oracle.HORIZONS:
            h = self.model["horizons"][label]
            self.assertEqual(h["direction"]["features"], oracle.DIRECTION_FEATURES)
            self.assertEqual(h["sigma"]["features"], oracle.SIGMA_FEATURES)
            self.assertIn("calibration", h["direction"])

    def test_window_features_match_full_history(self):
        data = series(2500)
        full = oracle.compute_features(data)[-1]
        window = oracle.compute_features(data[-1200:], only_last=True)[-1]
        for key in full:
            self.assertAlmostEqual(full[key], window[key], places=3, msg=key)

    def test_prediction_is_sane(self):
        data = series()
        f = oracle.predict(data, self.model)
        price = data[-1].close
        for label, h in f["horizons"].items():
            self.assertTrue(0.3 < h["p_up"] < 0.7)
            lo80, hi80 = h["bands"]["80"]
            lo50, hi50 = h["bands"]["50"]
            self.assertTrue(lo80 < lo50 < price < hi50 < hi80, label)
        self.assertLess(f["horizons"]["1h"]["bands"]["80"][1], f["horizons"]["4h"]["bands"]["80"][1])

    def test_too_few_candles(self):
        self.assertIsNone(oracle.predict(series(500), self.model))

    def test_record_and_resolve_scoreboard(self):
        data = series(1400)
        state = {}
        recorded = 0
        for j in range(1200, 1400):
            window = data[: j + 1]
            oracle.resolve(state, "BTCUSD", window)
            recorded += oracle.record(state, "BTCUSD", oracle.predict(window, self.model), 0.03)
        self.assertGreater(recorded, 40)
        s1 = state["oracle"]["score"]["BTCUSD:1h"]
        s4 = state["oracle"]["score"]["BTCUSD:4h"]
        self.assertGreater(s1["n"], s4["n"])
        self.assertTrue(0 <= s1["hits"] <= s1["n"])
        # una previsione non viene registrata due volte
        self.assertFalse(oracle.record(state, "BTCUSD", oracle.predict(data[:1400], self.model), 0.03)
                         and oracle.record(state, "BTCUSD", oracle.predict(data[:1400], self.model), 0.03))

    def test_lean(self):
        self.assertEqual(oracle.lean(0.54, 0.03), "up")
        self.assertEqual(oracle.lean(0.46, 0.03), "down")
        self.assertEqual(oracle.lean(0.52, 0.03), "none")


if __name__ == "__main__":
    unittest.main()
