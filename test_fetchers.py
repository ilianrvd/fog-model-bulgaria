# -*- coding: utf-8 -*-
"""
test_fetchers.py — тест на verify_forecast в двата фетчъра
==========================================================
Синтетични данни, без мрежа. Проверява трите неща, които Етап 1 промени
в iem_fetcher.py и ogimet_fetcher.py.

    python test_fetchers.py

F1  сдвояването е по наблюдения → половинчасовите METAR-и не се губят
F2  VIS = 0 при плътна мъгла вече не се чете като ясно небе
F3  едно наблюдение не обслужва два моделни записа
"""
import sys, os, io, types, contextlib
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import metar_parser            # noqa: F401
except ImportError:
    stub = types.ModuleType("metar_parser")
    stub.parse_metar = lambda raw: {}
    sys.modules["metar_parser"] = stub
    print("[тест] metar_parser липсва — ползвам заместител")

import iem_fetcher, ogimet_fetcher

DATE, HOUR0, HOURS = "2024-12-30", 18, 15
MODS = (("IEM", iem_fetcher), ("OGIMET", ogimet_fetcher))


def history(step_min):
    out, n = [], int(HOURS * 60 / step_min)
    for k in range(n + 1):
        th  = k * step_min / 60.0
        h   = (HOUR0 + th) % 24
        fog = (h >= 22) or (h <= 6)
        out.append({"time_h": th, "hour_utc": round(h, 1),
                    "vis_sfc": 300.0 if fog else 9000.0,
                    "T_sfc": 271.0, "cat": "IFR" if fog else "VFR"})
    return out


def obs(step_min, zero_at=None):
    out, n = [], int(HOURS * 60 / step_min)
    t0 = datetime(2024, 12, 30, HOUR0, 0)
    for k in range(n + 1):
        t   = t0 + timedelta(minutes=k * step_min)
        fog = (t.hour >= 23) or (t.hour <= 5)
        v   = 400 if fog else 9000
        if zero_at is not None and t.hour == zero_at:
            v = 0
        out.append({"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "vis_m": v, "T": -2.0, "raw": ""})
    return out


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def total(r):
    return sum(r[k] for k in ("hits", "misses", "false_alarms", "correct_neg"))


ok = True
print("\nF1  сдвояване по наблюдения — 30-мин METAR-и вече не се губят")
for name, mod in MODS:
    n60 = total(quiet(mod.verify_forecast, history(60), "LBGO", DATE, HOUR0,
                      {"LBGO": obs(60)}))
    n30 = total(quiet(mod.verify_forecast, history(30), "LBGO", DATE, HOUR0,
                      {"LBGO": obs(30)}))
    good = n30 == 2 * n60 - 1
    ok &= good
    print(f"  {name:<7} сдвоени: часов {n60} → 30-мин {n30}   "
          f"{'OK' if good else 'ПАДА'}")

print("\nF2  VIS = 0 при плътна мъгла вече не се чете като ясно")
for name, mod in MODS:
    ra = quiet(mod.verify_forecast, history(30), "LBGO", DATE, HOUR0,
               {"LBGO": obs(30)})
    rb = quiet(mod.verify_forecast, history(30), "LBGO", DATE, HOUR0,
               {"LBGO": obs(30, zero_at=2)})
    good = all(ra[k] == rb[k] for k in
               ("hits", "misses", "false_alarms", "correct_neg"))
    ok &= good
    print(f"  {name:<7} 400m: H={ra['hits']} FA={ra['false_alarms']}   "
          f"0m: H={rb['hits']} FA={rb['false_alarms']}   "
          f"{'OK — без промяна' if good else 'ПАДА — VIS=0 се губи'}")

print("\nF3  наблюдение не се използва два пъти (липсващ кръгъл час)")
o = [x for x in obs(30) if not x["time"].endswith("00:00Z")]
for name, mod in MODS:
    n = total(quiet(mod.verify_forecast, history(30), "LBGO", DATE, HOUR0,
                    {"LBGO": o}))
    good = n <= len(o)
    ok &= good
    print(f"  {name:<7} обси={len(o)} сдвоени={n}   "
          f"{'OK' if good else 'ПАДА — дублиране'}")

print("\n" + ("ВСИЧКИ ТЕСТОВЕ МИНАХА" if ok else "ИМА ПАДНАЛИ ТЕСТОВЕ"))
sys.exit(0 if ok else 1)
