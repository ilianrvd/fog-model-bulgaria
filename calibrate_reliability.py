# -*- coding: utf-8 -*-
"""
calibrate_reliability.py — извежда надеждността от набора
==========================================================
Чете features.csv, смята колко често всяка прогноза е вярна, и записва
reliability.json. Модулът reliability.py чете този файл.

    python calibrate_reliability.py
    python calibrate_reliability.py --thr 0.95

Числата НЕ се зашиват в кода. При разширяване на набора се пуска пак и
калибрацията се обновява.

Какво се мери
-------------
1. Базова надеждност: като моделът каже мъгла, колко често е прав?
   Като каже ясно?
2. Разслояване по ранната приземна влажност (максимум 18–22 UTC):
   променя ли се надеждността в различните режими?

Измерено на 288 случая (31.07.2026), континентални станции:
    казва МЪГЛА   33/67   49 %   (интервал 38–61)
    казва ЯСНО   125/149  84 %   (интервал 77–89)
  при разслояване на "ясно" по rh_early_max = 0.95:
    под прага   121/139  87 %
    над прага     4/10   40 %    Фишер p = 0.0013

Разслояването на "мъгла" НЕ е значимо (p = 0.077) и не се ползва.
"""
import sys, os, csv, json, math, argparse
from math import comb

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--out", default="reliability.json")
ap.add_argument("--thr", type=float, default=0.95,
                help="праг по rh_early_max за разслояване")
ap.add_argument("--min-n", type=int, default=25,
                help="под този брой групата се обявява за неоценена")
opt = ap.parse_args()

COASTAL = {"LBWN", "LBBG"}


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fisher(a, b, c, d):
    n = a + b + c + d
    if n == 0:
        return 1.0
    f = lambda a, b, c, d: comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
    obs = f(a, b, c, d)
    p = 0.0
    for i in range(0, min(a + b, a + c) + 1):
        j, k = a + b - i, a + c - i
        l = c + d - k
        if j < 0 or k < 0 or l < 0:
            continue
        t = f(i, j, k, l)
        if t <= obs + 1e-12:
            p += t
    return min(1.0, p)


rows = list(csv.DictReader(open(opt.csv, encoding="utf-8")))
print(f"  прочетени {len(rows)} случая")


def block(rs, label):
    """Надеждност за една група станции."""
    n_fog_obs = sum(1 for r in rs if r.get("cat") == "CFOG")
    out = {"label": label, "n_cases": len(rs), "n_fog_cases": n_fog_obs}
    for side, evs, good in (("fog", ("HIT", "FA"), "HIT"),
                            ("clear", ("MISS", "CN"), "CN")):
        s = [r for r in rs if r["event"] in evs]
        k = sum(1 for r in s if r["event"] == good)
        lo, hi = wilson(k, len(s))
        d = {"n": len(s), "correct": k,
             "rate": (k / len(s)) if s else None,
             "ci": [lo, hi], "usable": len(s) >= opt.min_n}
        # разслояване
        A = [r for r in s if (lambda v: v is not None and v < opt.thr)(
            num(r.get("rh_early_max")))]
        B = [r for r in s if (lambda v: v is not None and v >= opt.thr)(
            num(r.get("rh_early_max")))]
        ka = sum(1 for r in A if r["event"] == good)
        kb = sum(1 for r in B if r["event"] == good)
        p = fisher(ka, len(A) - ka, kb, len(B) - kb) if A and B else 1.0
        d["split"] = {
            "thr": opt.thr,
            "below": {"n": len(A), "correct": ka,
                      "rate": (ka / len(A)) if A else None,
                      "ci": list(wilson(ka, len(A)))},
            "above": {"n": len(B), "correct": kb,
                      "rate": (kb / len(B)) if B else None,
                      "ci": list(wilson(kb, len(B)))},
            "p_fisher": p,
            "significant": bool(p < 0.05 and len(A) >= 10 and len(B) >= 5)}
        out[side] = d
    return out


groups = {
    "continental": [r for r in rows if r["icao"] not in COASTAL],
    "coastal": [r for r in rows if r["icao"] in COASTAL],
}
for icao in sorted({r["icao"] for r in rows}):
    groups[icao] = [r for r in rows if r["icao"] == icao]

cal = {"threshold_feature": "rh_early_max",
       "threshold": opt.thr,
       "n_total": len(rows),
       "groups": {}}
for g, rs in groups.items():
    cal["groups"][g] = block(rs, g)

json.dump(cal, open(opt.out, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ── отчет
print(f"\n{'='*84}")
print(f"  НАДЕЖДНОСТ НА ПРОГНОЗАТА   праг за разслояване: "
      f"rh_early_max = {opt.thr}")
print(f"{'='*84}")
for g in ("continental", "coastal", "LBSF", "LBGO", "LBPD", "LBWN", "LBBG"):
    if g not in cal["groups"]:
        continue
    b = cal["groups"][g]
    nf = b.get("n_fog_cases", 0)
    warn = "   ← НУЛА мъглени случая" if nf == 0 else ""
    print(f"\n  {g}  ({b['n_cases']} случая, {nf} мъглени){warn}")
    for side, name in (("fog", "казва МЪГЛА"), ("clear", "казва ЯСНО")):
        d = b[side]
        if d["n"] == 0:
            print(f"    {name:<14} няма случаи")
            continue
        u = "" if d["usable"] else "   ← под прага за оценка"
        print(f"    {name:<14} {d['correct']:>3}/{d['n']:<4} "
              f"{d['rate']:>5.0%}   интервал {d['ci'][0]:.0%}–{d['ci'][1]:.0%}{u}")
        sp = d["split"]
        if sp["below"]["n"] and sp["above"]["n"]:
            mark = "  ЗНАЧИМО" if sp["significant"] else ""
            print(f"      под {opt.thr:.2f}: {sp['below']['correct']:>3}/"
                  f"{sp['below']['n']:<4} "
                  f"{sp['below']['rate']:>5.0%}   "
                  f"над: {sp['above']['correct']:>3}/{sp['above']['n']:<4} "
                  f"{sp['above']['rate']:>5.0%}   "
                  f"p={sp['p_fisher']:.4f}{mark}")

print(f"\n{'='*84}")
print(f"  [JSON] {opt.out}")
print(f"  Ползва се от reliability.py. При разширяване на набора —")
print(f"  пусни collect_features.py, после този скрипт.")
print(f"{'='*84}")
