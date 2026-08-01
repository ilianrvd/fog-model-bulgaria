# -*- coding: utf-8 -*-
"""
apply_rule.py — цената на кандидат-правилото
=============================================
Чете features.csv. Нула пускания на модела.

    python apply_rule.py
    python apply_rule.py --feature rh_early_max --thr 0.93 --below-suppress

Правилото се прилага върху ВСИЧКИ случаи, в които моделът е обявил
мъгла — не само върху сухите ясни нощи, от които е изведено. Това е
липсващата половина от сметката: досега знаехме колко фалшиви аларми
хваща, но не и колко реални мъгли убива.

  HIT  → MISS   загуба (моделът е бил прав, правилото го отменя)
  FA   → CN     печалба
  MISS, CN      не се променят (моделът не е обявявал мъгла)

Отчита се по група и по станция, с пълната матрица преди и след,
плюс CSI.
"""
import sys, os, csv, math, argparse
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--feature", default="rh_early_max")
ap.add_argument("--thr", type=float, default=None,
                help="праг; по подразбиране се пробват няколко")
ap.add_argument("--scan", nargs="*", type=float,
                default=[0.90, 0.92, 0.93, 0.94, 0.95, 0.96])
ap.add_argument("--group", choices=["all", "cont", "coast"], default="cont")
opt = ap.parse_args()

rows = list(csv.DictReader(open(opt.csv, encoding="utf-8")))
COASTAL = {"LBWN", "LBBG"}


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def pick(g):
    if g == "cont":
        return [r for r in rows if r["icao"] not in COASTAL]
    if g == "coast":
        return [r for r in rows if r["icao"] in COASTAL]
    return rows


def matrix(rs, rule=None):
    """rule(r) -> True значи 'потисни прогнозата за мъгла'."""
    c = dict(HIT=0, MISS=0, FA=0, CN=0)
    moved = dict(hit_to_miss=[], fa_to_cn=[], no_data=[])
    for r in rs:
        ev = r["event"]
        if rule is not None and ev in ("HIT", "FA"):
            v = num(r.get(opt.feature))
            if v is None:
                moved["no_data"].append(r["case"])
            elif rule(v):
                if ev == "HIT":
                    moved["hit_to_miss"].append(r["case"]); ev = "MISS"
                else:
                    moved["fa_to_cn"].append(r["case"]); ev = "CN"
        c[ev] += 1
    return c, moved


def csi(c):
    d = c["HIT"] + c["MISS"] + c["FA"]
    return c["HIT"] / d if d else float("nan")


def show(c, tag):
    return (f"{tag:<10} HIT={c['HIT']:>3} MISS={c['MISS']:>3} "
            f"FA={c['FA']:>3} CN={c['CN']:>4}  CSI={csi(c):.3f}")


sub = pick(opt.group)
base, _ = matrix(sub)
print(f"\n{'='*84}")
print(f"  ЦЕНА НА ПРАВИЛОТО   признак: {opt.feature}   група: {opt.group}")
print(f"  Правило: ако {opt.feature} < праг → отхвърли прогнозата за мъгла")
print(f"{'='*84}")
print(f"  {show(base, 'СЕГА')}   ({len(sub)} случая)")

# разпределение на признака по изход
print(f"\n  Разпределение на {opt.feature}")
print(f"    {'изход':<6} {'n':>4} {'p10':>8} {'p50':>8} {'p90':>8} {'мин':>8}")
for ev in ("HIT", "MISS", "FA", "CN"):
    v = sorted(x for x in (num(r.get(opt.feature)) for r in sub
                           if r["event"] == ev) if x is not None)
    if not v:
        continue
    q = lambda p: v[max(0, min(len(v)-1, int(round(p*(len(v)-1)))))]
    print(f"    {ev:<6} {len(v):>4} {q(.1):>8.3f} {q(.5):>8.3f} "
          f"{q(.9):>8.3f} {min(v):>8.3f}")

print(f"\n  {'праг':>6} {'HIT':>5} {'MISS':>5} {'FA':>5} {'CN':>5} "
      f"{'CSI':>7} {'ΔCSI':>7} {'убити':>6} {'спасени':>8}")
print(f"  {'':>6} {'':>5} {'':>5} {'':>5} {'':>5} {'':>7} {'':>7} "
      f"{'мъгли':>6} {'ФА':>8}")
thrs = [opt.thr] if opt.thr is not None else opt.scan
best = None
for t in thrs:
    c, mv = matrix(sub, rule=lambda v, t=t: v < t)
    d = csi(c) - csi(base)
    print(f"  {t:>6.2f} {c['HIT']:>5} {c['MISS']:>5} {c['FA']:>5} "
          f"{c['CN']:>5} {csi(c):>7.3f} {d:>+7.3f} "
          f"{len(mv['hit_to_miss']):>6} {len(mv['fa_to_cn']):>8}")
    if best is None or d > best[1]:
        best = (t, d, c, mv)

t, d, c, mv = best
print(f"\n{'='*84}")
print(f"  НАЙ-ДОБЪР ПРАГ: {t:.2f}   ΔCSI = {d:+.3f}")
print(f"{'='*84}")
if mv["hit_to_miss"]:
    print(f"  Убити реални мъгли ({len(mv['hit_to_miss'])}):")
    for cs in mv["hit_to_miss"]:
        v = num(next(r[opt.feature] for r in sub if r["case"] == cs))
        print(f"    {cs:<28} {opt.feature}={v:.3f}")
else:
    print("  Убити реални мъгли: няма")
print(f"\n  Спасени фалшиви аларми ({len(mv['fa_to_cn'])}):")
for cs in mv["fa_to_cn"][:20]:
    v = num(next(r[opt.feature] for r in sub if r["case"] == cs))
    print(f"    {cs:<28} {opt.feature}={v:.3f}")
if len(mv["fa_to_cn"]) > 20:
    print(f"    ... и още {len(mv['fa_to_cn'])-20}")
if mv["no_data"]:
    print(f"\n  Без стойност за признака ({len(mv['no_data'])}) — "
          f"правилото не се прилага:")
    for cs in mv["no_data"][:10]:
        print(f"    {cs}")

# по станция при най-добрия праг
print(f"\n  ПО СТАНЦИЯ при праг {t:.2f}")
print(f"    {'ст':<6} {'преди':>26} {'след':>26} {'ΔCSI':>7}")
for icao in ("LBSF", "LBGO", "LBPD", "LBWN", "LBBG"):
    s = [r for r in rows if r["icao"] == icao]
    if not s:
        continue
    b0, _ = matrix(s)
    a0, _ = matrix(s, rule=lambda v, t=t: v < t)
    f = lambda c: f"H{c['HIT']}/M{c['MISS']}/F{c['FA']}/C{c['CN']}"
    dd = csi(a0) - csi(b0)
    dd_s = "  —" if math.isnan(dd) else f"{dd:+7.3f}"
    print(f"    {icao:<6} {f(b0):>26} {f(a0):>26} {dd_s:>7}")

print(f"\n  ВНИМАНИЕ: прагът е изведен от ТЕЗИ данни. Реалната стойност")
print(f"  на правилото е тази от validate_split.py, не тази тук.")
