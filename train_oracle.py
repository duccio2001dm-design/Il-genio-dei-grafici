"""
Addestra e verifica l'oracolo. Non serve al bot per girare: serve solo per
rifare i pesi (oracle_model.json) quando vuoi aggiornarli con dati nuovi.

Richiede numpy e scikit-learn:   pip install numpy scikit-learn
Uso:   python train_oracle.py candele_15m.csv

Il CSV deve avere le colonne: ts (ms UTC apertura), open, high, low, close, volume.

Procedura:
  1. calcola le feature con lo stesso codice usato dal vivo (oracle.compute_features);
  2. verifica walk-forward: per ogni anno addestra solo sugli anni precedenti e
     misura sull'anno successivo, che il modello non ha mai visto;
  3. addestra il modello finale su tutto lo storico e salva pesi e risultati.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import namedtuple
from datetime import datetime, timezone

import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression

import oracle

Candle = namedtuple("Candle", "ts open high low close volume")
THRESHOLDS = (0.02, 0.03, 0.04, 0.05)
C_REG = 0.05


def load(path):
    with open(path, newline="") as fh:
        return [Candle(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r["volume"])) for r in csv.DictReader(fh)]


def fit_direction(X, y):
    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std == 0] = 1.0
    m = LogisticRegression(C=C_REG, max_iter=3000).fit((X - mean) / std, y)
    return {"features": oracle.DIRECTION_FEATURES, "mean": mean.tolist(), "std": std.tolist(),
            "coef": m.coef_[0].tolist(), "intercept": float(m.intercept_[0])}


def fit_sigma(X, ret):
    m = LinearRegression().fit(X, np.log(ret ** 2 + 1e-10))
    pred_sigma = np.exp(m.predict(X) / 2)
    z = ret / pred_sigma
    recent = z[-200000:]
    q = {f"q{p}": float(np.quantile(recent, p / 100)) for p in (10, 25, 75, 90)}
    return {"features": oracle.SIGMA_FEATURES, "coef": m.coef_.tolist(), "intercept": float(m.intercept_),
            "z_quantiles": q, "median_sigma": float(np.median(pred_sigma[-35000:]))}


def apply_direction(model, X):
    z = ((X - np.array(model["mean"])) / np.array(model["std"])) @ np.array(model["coef"]) + model["intercept"]
    return 1 / (1 + np.exp(-z))


def apply_sigma(model, X):
    return np.exp((X @ np.array(model["coef"]) + model["intercept"]) / 2)


def main(path):
    candles = load(path)
    print(f"{len(candles)} candele, dal {_d(candles[0].ts)} al {_d(candles[-1].ts)}")
    feats = oracle.compute_features(candles)
    closes = np.array([c.close for c in candles])
    years = np.array([datetime.fromtimestamp(c.ts / 1000, timezone.utc).year for c in candles])
    valid = np.array([f is not None for f in feats])

    out = {"trained_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "data_from": _d(candles[0].ts), "data_to": _d(candles[-1].ts),
           "source": "Bitstamp BTC/USD", "horizons": {}, "validation": {}}

    for label, k in oracle.HORIZONS.items():
        fwd = np.full(len(candles), np.nan)
        fwd[:-k] = np.log(closes[k:] / closes[:-k])
        ok = valid & ~np.isnan(fwd)
        idx = np.where(ok)[0]
        Xd = np.array([[feats[i][n] for n in oracle.DIRECTION_FEATURES] for i in idx])
        Xs = np.array([[feats[i][n] for n in oracle.SIGMA_FEATURES] for i in idx])
        r, y, yr = fwd[idx], (fwd[idx] > 0).astype(int), years[idx]

        rows = []
        oos_p, oos_y, oos_z, oos_year = [], [], [], []
        for test_year in range(max(2021, yr.min() + 2), yr.max() + 1):
            tr = np.where(yr < test_year)[0][:-k]          # embargo: niente target sovrapposti
            te = np.where(yr == test_year)[0]
            dm, sm = fit_direction(Xd[tr], y[tr]), fit_sigma(Xs[tr], r[tr])
            p, sig = apply_direction(dm, Xd[te]), apply_sigma(sm, Xs[te])
            q = sm["z_quantiles"]
            in80 = (r[te] >= q["q10"] * sig) & (r[te] <= q["q90"] * sig)
            in50 = (r[te] >= q["q25"] * sig) & (r[te] <= q["q75"] * sig)
            oos_p.append(p); oos_y.append(y[te]); oos_z.append(r[te] / sig); oos_year.append(yr[te])
            base = y[tr].mean()
            row = {"year": int(test_year), "n": int(len(te)),
                   "acc": float(((p > 0.5) == y[te]).mean()),
                   "brier_skill": float(1 - np.mean((p - y[te]) ** 2) / np.mean((base - y[te]) ** 2)),
                   "cover80": float(in80.mean()), "cover50": float(in50.mean())}
            for t in THRESHOLDS:
                sel = np.abs(p - 0.5) >= t
                row[f"lean_share_{t}"] = float(sel.mean())
                row[f"lean_acc_{t}"] = float(((p[sel] > 0.5) == y[te][sel]).mean()) if sel.any() else None
            rows.append(row)
            print(f"{label} {test_year}: giuste {row['acc']:.3f}  forbice80 {row['cover80']:.3f}  "
                  f"forbice50 {row['cover50']:.3f}  " +
                  "  ".join(f"|p-0.5|>={t}: {row[f'lean_share_{t}']*100:.0f}% dei casi, giuste "
                            f"{row[f'lean_acc_{t}'] or 0:.3f}" for t in THRESHOLDS))
        # Calibrazione: logit(p_vera) = a * logit(p_modello) + b, stimata solo su
        # previsioni fuori campione. Rimedia all'eccesso di sicurezza del modello.
        P, Y, Z, YR = map(np.concatenate, (oos_p, oos_y, oos_z, oos_year))
        logit = np.log(P / (1 - P)).reshape(-1, 1)
        cal = LogisticRegression(C=1e6).fit(logit, Y)
        a, b = float(cal.coef_[0][0]), float(cal.intercept_[0])
        Pc = 1 / (1 + np.exp(-(a * logit[:, 0] + b)))
        print(f"{label}: calibrazione a={a:.3f} b={b:.4f}")
        for row in rows:
            m = YR == row["year"]
            row["acc_cal"] = float(((Pc[m] > 0.5) == Y[m]).mean())
            base = 0.5
            row["brier_skill_cal"] = float(1 - np.mean((Pc[m] - Y[m]) ** 2) / np.mean((Y[m].mean() - Y[m]) ** 2))
            for t in (0.01, 0.015, 0.02, 0.03):
                sel = np.abs(Pc[m] - 0.5) >= t
                row[f"cal_lean_share_{t}"] = float(sel.mean())
                row[f"cal_lean_acc_{t}"] = float(((Pc[m][sel] > 0.5) == Y[m][sel]).mean()) if sel.any() else None
            print(f"  {row['year']}: brier skill {row['brier_skill_cal']:+.4f}  " + "  ".join(
                f"|p-0.5|>={t}: {row[f'cal_lean_share_{t}']*100:.0f}% casi, giuste {row[f'cal_lean_acc_{t}'] or 0:.3f}"
                for t in (0.01, 0.015, 0.02, 0.03)))
        # reliability per fasce
        bins = [0, 0.47, 0.49, 0.51, 0.53, 1]
        rel = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            m = (Pc >= lo) & (Pc < hi)
            if m.sum() > 100:
                rel.append({"from": lo, "to": hi, "n": int(m.sum()), "predicted": float(Pc[m].mean()), "observed": float(Y[m].mean())})
        print(f"  affidabilita': " + "  ".join(f"prevista {x['predicted']:.3f} osservata {x['observed']:.3f} (n={x['n']})" for x in rel))
        out["validation"][label] = {"years": rows, "reliability": rel}

        recent_z = Z[YR >= YR.max() - 1]
        last = np.where(yr >= yr.min())[0][:-k]
        direction = fit_direction(Xd[last], y[last])
        direction["calibration"] = {"a": a, "b": b}
        sigma = fit_sigma(Xs[last], r[last])
        # quantili delle forbici presi dagli errori fuori campione degli ultimi due anni
        sigma["z_quantiles"] = {f"q{p}": float(np.quantile(recent_z, p / 100)) for p in (10, 25, 75, 90)}
        out["horizons"][label] = {"k": k, "direction": direction, "sigma": sigma}

    with open(oracle.MODEL_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"Pesi salvati in {oracle.MODEL_PATH}")


def _d(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
