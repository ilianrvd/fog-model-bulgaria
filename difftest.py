# -*- coding: utf-8 -*-
"""
difftest.py — диференциален тест на Етап 1
==========================================
Сравнява СТАРАТА и НОВАТА evaluate върху реални METAR-и и синтетична
моделна история. Не пипа ICON кеша и не иска мрежа.

Подготовка
----------
Преди да презапишеш verify_cases.py, запази стария:

    copy verify_cases.py verify_cases_OLD.py       (Windows)
    cp   verify_cases.py verify_cases_OLD.py       (Linux/Mac)

Употреба
--------
    python difftest.py cases\\LBGO_CFOG_2024-12-30.txt cases\\LBGO_CDRY_2025-03-07.txt

Или върху цялата папка:

    python difftest.py cases\\*.txt

Какво е важно
-------------
K1 — при ЧАСОВА моделна история новият код трябва да даде ТОЧНО старото.
Ако K1 пада, кръпката е сменила дефиниция, не само резолюция → връщай се.
Колоната „НОВ, 30-мин" показва какво носи каденцата.
"""
import sys, os, glob, importlib.util
from datetime import datetime, timedelta, timezone

# Windows: при пренасочване (`> файл`) stdout минава на cp1252, която няма
# кирилица. Налагаме UTF-8, за да работи и на екран, и във файл.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

UTC  = timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
OLD_PATH = os.path.join(HERE, "verify_cases_OLD.py")
NEW_PATH = os.path.join(HERE, "verify_cases.py")


def load_mod(name, path):
    if not os.path.exists(path):
        sys.exit(f"Липсва {path}\n"
                 f"Запази стария файл като verify_cases_OLD.py преди да "
                 f"презапишеш verify_cases.py.")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


sys.path.insert(0, HERE)
NEW = load_mod("vc_new", NEW_PATH)
OLD = load_mod("vc_old", OLD_PATH)


def synth_history(t0, hours, step_min, vis_fn, t_fn):
    n = int(hours * 60 / step_min)
    hist = []
    for k in range(n + 1):
        th = k * step_min / 60.0
        t  = t0 + timedelta(hours=th)
        hist.append({
            "time_h"  : th,
            "hour_utc": round(t.hour + t.minute / 60.0, 1),
            "vis_sfc" : float(vis_fn(th)),
            "T_sfc"   : float(t_fn(th)) + 273.15,
            "cat"     : "VFR",
        })
    return hist


def line(tag, res):
    h = res["hourly"]
    csi = "—" if h["CSI"] is None else f"{h['CSI']:.3f}"
    tme = "—" if res["T"]["Tmin_err"] is None else f"{res['T']['Tmin_err']:.2f}"
    ons = "—" if res["onset_dt_h"] is None else f"{res['onset_dt_h']:.1f}"
    n   = sum(h[k] for k in ("hits", "misses", "fa", "cn"))
    return (f"  {tag:<22} {res['event']:<5} onset={ons:>5}  "
            f"H/M/FA/CN={h['hits']:>2}/{h['misses']:>2}/{h['fa']:>2}/{h['cn']:>2}"
            f"  n={n:>3}  CSI={csi:>5}  Tmin_err={tme:>6}")


def run(path):
    icao, cat, date_str, obs = NEW.load_case_file(path)
    hour = NEW.START_HOUR
    t0   = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour, tzinfo=UTC)

    # Синтетичен "модел": мъгла 22–06 UTC, права температурна крива.
    # Целта не е реализъм, а еднакъв вход за двете реализации.
    def vis(th):
        h = (hour + th) % 24
        return 300.0 if (h >= 22 or h <= 6) else 9000.0

    def temp(th):
        return 6.0 - 8.0 * min(th, 12.0) / 12.0

    h60 = synth_history(t0, NEW.FORECAST_H, 60, vis, temp)
    h30 = synth_history(t0, NEW.FORECAST_H, 30, vis, temp)

    r_old = OLD.evaluate(h60, obs, hour, date_str)
    r_new = NEW.evaluate(h60, obs, hour, date_str)
    r_30  = NEW.evaluate(h30, obs, hour, date_str)

    same = (r_old["event"] == r_new["event"]
            and r_old["onset_dt_h"] == r_new["onset_dt_h"]
            and all(r_old["hourly"][k] == r_new["hourly"][k]
                    for k in ("hits", "misses", "fa", "cn")))

    print(f"\n{icao} {cat} {date_str}   ({len(obs)} METAR-а)")
    print(line("СТАР, часова",  r_old))
    print(line("НОВ,  часова",  r_new))
    print(line("НОВ,  30-мин",  r_30))
    print(f"  K1: {'ИДЕНТИЧНО' if same else '*** РАЗЛИЧНО ***'}")
    return same, r_old, r_30


paths = []
for a in sys.argv[1:]:
    paths.extend(sorted(glob.glob(a)) or [a])
if not paths:
    sys.exit("Употреба: python difftest.py cases\\*.txt")

ok, flips, n_old, n_new = True, [], 0, 0
broken = []
for p in paths:
    try:
        s, ro, r3 = run(p)
    except Exception as e:
        print(f"\n{os.path.basename(p)}: ГРЕШКА — {e}")
        broken.append((os.path.basename(p), str(e)))
        ok = False
        continue
    ok &= s
    if ro["event"] != r3["event"]:
        flips.append((os.path.basename(p), ro["event"], r3["event"]))
        print(f"  → event се обръща: {ro['event']} → {r3['event']}")
    n_old += sum(ro["hourly"][k] for k in ("hits", "misses", "fa", "cn"))
    n_new += sum(r3["hourly"][k] for k in ("hits", "misses", "fa", "cn"))

print(f"\n{'='*72}")
print(f"  случаи                        : {len(paths)}")
print(f"  K1 (стар == нов, часов вход)  : {'МИНАВА' if ok else 'ПАДА'}")
print(f"  сдвоени наблюдения            : {n_old} → {n_new}"
      f"  (+{100*(n_new-n_old)/max(n_old,1):.0f} %)")
print(f"  случаи с обърнат event        : {len(flips)}")
if flips:
    from collections import Counter
    tally = Counter(f"{a} → {b}" for _, a, b in flips)
    print(f"\n  РАЗПРЕДЕЛЕНИЕ НА ОБРЪЩАНИЯТА")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"    {k:<14} {v}")
    print(f"\n  ПОИМЕННО")
    for name, a, b in sorted(flips, key=lambda x: (x[1], x[2], x[0])):
        print(f"    {name:<34} {a:>4} → {b}")
if broken:
    print(f"\n  СЛУЧАИ С ГРЕШКА: {len(broken)}")
    for name, e in broken:
        print(f"    {name:<34} {e}")
print(f"{'='*72}")
sys.exit(0 if ok else 1)
