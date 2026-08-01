# -*- coding: utf-8 -*-
"""
make_case.py — построяване на нови case файлове от OGIMET
==========================================================
Ползва СЪЩИЯ case_rules.py като relabel_cases.py — един източник за
разбора, статистиките и категоризацията.

    python make_case.py LBWN 2026-07-20 2026-07-27
    python make_case.py LBWN 2026-07-20 2026-07-27 --write
    python make_case.py LBWN LBBG LBSF 2026-07-20 2026-07-27 --write

По подразбиране НЕ пише — показва какво би създал и с каква категория.

Прозорецът е 16:00 UTC на датата до 09:00 UTC на следващия ден, както
при съществуващите случаи. Нощта се именува по НАЧАЛНАТА дата.

Пази състояние в make_case_state.json и продължава след прекъсване —
OGIMET има 25 s пауза на заявка, тоест ~30 мин за 24 нощи.
"""
import sys, os, json, glob, argparse, io, contextlib
from datetime import datetime, timedelta

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import case_rules as cr

ap = argparse.ArgumentParser()
ap.add_argument("args", nargs="+", help="ICAO... начална_дата крайна_дата")
ap.add_argument("--cases-dir", default="cases")
ap.add_argument("--write", action="store_true")
ap.add_argument("--force", action="store_true",
                help="презаписва, ако вече има случай за тази нощ")
ap.add_argument("--min-metars", type=int, default=20,
                help="нощи с по-малко наблюдения се пропускат")
ap.add_argument("--state", default="make_case_state.json")
opt = ap.parse_args()

*icaos, d_start, d_end = opt.args
if not icaos:
    sys.exit("Употреба: python make_case.py LBWN [LBBG ...] 2026-07-20 2026-07-27")

# Мързелив внос: ogimet_fetcher внася metar_parser на модулно ниво,
# а той не е нужен, когато работим от кеша. Разборът е в case_rules.
_fetch = None
def fetch(icao, ds):
    global _fetch
    if _fetch is None:
        from ogimet_fetcher import fetch_metar_ogimet as _f
        _fetch = _f
    return _fetch(icao, ds, hour0=cr.NIGHT_START_UTC, hours=18)

state = {}
if os.path.exists(opt.state):
    try:
        state = json.load(open(opt.state, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}

d0 = datetime.strptime(d_start, "%Y-%m-%d")
d1 = datetime.strptime(d_end,   "%Y-%m-%d")
dates = [(d0 + timedelta(days=k)).strftime("%Y-%m-%d")
         for k in range((d1 - d0).days + 1)]

# вече съществуващи случаи — ICAO+дата, независимо от категорията
existing = {}
for p in glob.glob(os.path.join(opt.cases_dir, "*.txt")):
    stem = os.path.splitext(os.path.basename(p))[0]
    parts = stem.split("_")
    if len(parts) == 3:
        existing[(parts[0], parts[2])] = stem

plan, skipped = [], []

for icao in icaos:
    for ds in dates:
        key = f"{icao}_{ds}"
        if (icao, ds) in existing and not opt.force:
            skipped.append((key, f"вече има {existing[(icao, ds)]}"))
            continue

        cached = state.get(key)
        if cached and cached.get("raws"):
            raws = cached["raws"]
        else:
            print(f"  [OGIMET] {icao} {ds} ...", flush=True)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    recs = fetch(icao, ds)   # 16:00 → +18 h, с буфер
                raws = [r["raw"] for r in recs if r.get("raw")]
            except Exception as e:
                skipped.append((key, f"OGIMET: {str(e)[:50]}"))
                continue
            state[key] = {"raws": raws}
            json.dump(state, open(opt.state, "w", encoding="utf-8"),
                      ensure_ascii=False)

        t0, t1 = cr.night_window(ds)
        obs = [o for o in (cr.parse_metar(r, ds) for r in raws)
               if o and t0 <= o["dt"] <= t1]
        if len(obs) < opt.min_metars:
            skipped.append((key, f"само {len(obs)} наблюдения"))
            continue

        st = cr.stats(obs)
        cat = cr.classify(st)
        plan.append(dict(icao=icao, ds=ds, cat=cat, st=st, obs=obs))

# ── Отчет
print(f"\n{'='*82}")
print(f"  нощи за създаване : {len(plan)}")
print(f"  пропуснати        : {len(skipped)}")
print(f"{'='*82}")

if plan:
    print(f"\n  {'случай':<28} {'n':>3} {'vis_min':>8} {'явл':>4} {'<1km':>5} "
          f"{'вятър':>6} {'ЯДРО':>6} {'OVC':>5} {'спред':>6}")
    print(f"  {'':<28} {'':>3} {'':>8} {'':>4} {'':>5} "
          f"{'16-09':>6} {'20-06':>6}   ← решава ЯДРОТО")
    for r in plan:
        st = r["st"]
        vm = "—" if st["vis_min"] is None else f"{st['vis_min']:.0f}"
        sp = "—" if st["spread_min"] is None else f"{st['spread_min']:.1f}"
        ovc = st["n_lowovc"] / st["n"] if st["n"] else 0.0
        print(f"  {r['icao']}_{r['cat']}_{r['ds']:<10} {st['n']:>3} {vm:>8} "
              f"{st['n_fogwx']:>4} {st['n_fogvis']:>5} "
              f"{st['wind_max']:>5.0f}k {st['wind_max_core']:>5.0f}k "
              f"{ovc:>5.0%} {sp:>6}")

    from collections import Counter
    print(f"\n  по категория: {dict(Counter(r['cat'] for r in plan))}")

if skipped:
    print(f"\n  ПРОПУСНАТИ")
    for k, why in skipped[:20]:
        print(f"    {k:<24} {why}")
    if len(skipped) > 20:
        print(f"    ... и още {len(skipped)-20}")

if not opt.write:
    print(f"\n{'='*82}")
    print("  ПРЕГЛЕД — нищо не е записано.  За запис добави --write")
    print(f"{'='*82}")
    sys.exit(0)

os.makedirs(opt.cases_dir, exist_ok=True)
n = 0
for r in plan:
    stem = f"{r['icao']}_{r['cat']}_{r['ds']}"
    path = os.path.join(opt.cases_dir, stem + ".txt")
    text = cr.build_case_text(r["icao"], r["cat"], r["ds"], r["obs"])
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)
    print(f"  записан: {path}  ({r['st']['n']} METAR-а)")
    n += 1

print(f"\n{'='*82}")
print(f"  СЪЗДАДЕНИ: {n} случая в {opt.cases_dir}\\")
print("  Пусни пълен пробег и запечатай нов репер.")
print(f"{'='*82}")
