# -*- coding: utf-8 -*-
"""
infer_rules.py — извличане на СЪЩЕСТВУВАЩИТЕ прагове от 264-те етикета
=======================================================================
Не преетикетира нищо. Смята статистики за всеки случай и показва как
се разпределят по НАСТОЯЩАТА категория, за да се видят реалните
граници, по които наборът е бил построен.

    python infer_rules.py cases\\*.txt
    python infer_rules.py cases\\*.txt --dump stats.csv

Повод: първият опит за преетикетиране смени 100 от 264 случая, но
90 от тях бяха въртележка CLDY↔DYNM↔CDRY — тоест съчинени прагове,
не поправен дефект. Дефектът е само в разпознаването на мъгла.
"""
import sys, os, re, glob, argparse
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_rules as cr

ap = argparse.ArgumentParser()
ap.add_argument("patterns", nargs="+")
ap.add_argument("--dump", default=None)
args = ap.parse_args()

paths = []
for p in args.patterns:
    paths.extend(sorted(glob.glob(p)) or [p])

NAME_RE = re.compile(r"^(LB[A-Z]{2})_(CFOG|CDRY|CLDY|DYNM)_(\d{4}-\d{2}-\d{2})$")
rows = []

for path in paths:
    stem = os.path.splitext(os.path.basename(path))[0]
    m = NAME_RE.match(stem)
    if not m:
        continue
    icao, cat, ds = m.groups()
    raws = cr.load_raw_metars(path)
    t0, t1 = cr.night_window(ds)
    obs = [o for o in (cr.parse_metar(r, ds) for r in raws)
           if o and t0 <= o["dt"] <= t1]
    if not obs:
        continue
    st = cr.stats(obs)
    rows.append({
        "case": stem, "icao": icao, "cat": cat, "ds": ds,
        "n": st["n"],
        "wind_max": st["wind_max"], "wind_mean": st["wind_mean"],
        "wind_max_core": st["wind_max_core"],
        "wind_mean_core": st["wind_mean_core"],
        "gust_max": st["gust_max"] or 0.0,
        "vis_min": st["vis_min"] if st["vis_min"] is not None else 10000.0,
        "n_fogwx": st["n_fogwx"], "n_fogvis": st["n_fogvis"],
        "n_lowvis": st["n_lowvis"], "n_precip": st["n_precip"],
        "ovc_frac": st["n_lowovc"] / st["n"] if st["n"] else 0.0,
        "clear_pct": st["clear_pct"],
        "spread_min": st["spread_min"] if st["spread_min"] is not None else 99.0,
    })

if not rows:
    sys.exit("Нула разборени случаи.")


def pct(vals, q):
    v = sorted(vals)
    if not v:
        return float("nan")
    i = max(0, min(len(v) - 1, int(round(q * (len(v) - 1)))))
    return v[i]


FIELDS = [("wind_max", "макс. вятър [kt]"),
          ("wind_max_core", "макс. вятър ЯДРО 20-06 [kt]"),
          ("gust_max", "макс. порив [kt]"),
          ("ovc_frac", "дял ниска BKN/OVC"),
          ("n_precip", "METAR-и с валеж"),
          ("vis_min",  "мин. видимост [m]"),
          ("n_fogwx",  "METAR-и с явление"),
          ("n_fogvis", "METAR-и < 1000 m"),
          ("n_lowvis", "METAR-и < 2000 m")]

by_cat = defaultdict(list)
for r in rows:
    by_cat[r["cat"]].append(r)

print(f"\n{'='*80}")
print(f"  РАЗПРЕДЕЛЕНИЯ ПО НАСТОЯЩА КАТЕГОРИЯ  ({len(rows)} случая)")
print(f"{'='*80}")
for key, label in FIELDS:
    print(f"\n  {label}")
    print(f"    {'кат':<6} {'n':>4} {'min':>8} {'p10':>8} {'p50':>8} "
          f"{'p90':>8} {'max':>8}")
    for cat in ("CFOG", "CDRY", "CLDY", "DYNM"):
        v = [r[key] for r in by_cat.get(cat, [])]
        if not v:
            continue
        print(f"    {cat:<6} {len(v):>4} {min(v):>8.1f} {pct(v,0.10):>8.1f} "
              f"{pct(v,0.50):>8.1f} {pct(v,0.90):>8.1f} {max(v):>8.1f}")

# ── Кандидат-разделители
print(f"\n{'='*80}")
print("  КАНДИДАТ-ПРАГОВЕ (търси се стойност, разделяща двойките категории)")
print(f"{'='*80}")


def best_split(field, cat_hi, cat_lo, lo=None, hi=None):
    """Праг, максимизиращо разделящ cat_hi (над) от cat_lo (под)."""
    A = [r[field] for r in by_cat.get(cat_hi, [])]
    B = [r[field] for r in by_cat.get(cat_lo, [])]
    if not A or not B:
        return None
    cand = sorted(set(A + B))
    best, bt = -1.0, None
    for t in cand:
        acc = (sum(1 for x in A if x >= t) + sum(1 for x in B if x < t)) \
              / (len(A) + len(B))
        if acc > best:
            best, bt = acc, t
    return bt, best, len(A), len(B)


for field, hi_cat, lo_cat in (("wind_max", "DYNM", "CLDY"),
                              ("wind_max", "DYNM", "CDRY"),
                              ("wind_max_core", "DYNM", "CDRY"),
                              ("wind_max_core", "DYNM", "CLDY"),
                              ("gust_max", "DYNM", "CLDY"),
                              ("ovc_frac", "CLDY", "CDRY"),
                              ("n_precip", "DYNM", "CLDY")):
    r = best_split(field, hi_cat, lo_cat)
    if r:
        t, acc, na, nb = r
        print(f"  {field:<10} {hi_cat} ≥ t  срещу  {lo_cat} < t   "
              f"→ t = {t:>7.2f}   точност {acc:.1%}   (n={na}/{nb})")

# ── Мъглата: кои CFOG случаи биха се загубили при праг N
print(f"\n{'='*80}")
print("  ПРАГ ЗА CFOG — колко от сегашните CFOG оцеляват")
print(f"{'='*80}")
cf = by_cat.get("CFOG", [])
print(f"  общо CFOG: {len(cf)}")
for n in (1, 2, 3):
    keep = sum(1 for r in cf if r["n_fogwx"] >= n or r["n_fogvis"] >= n)
    print(f"    праг ≥{n} (явление ИЛИ <1000 m): {keep}/{len(cf)} "
          f"({100*keep/max(len(cf),1):.0f}%)")
for v in (200, 500, 1000):
    keep = sum(1 for r in cf if r["vis_min"] < v)
    print(f"    само vis_min < {v} m: {keep}/{len(cf)} "
          f"({100*keep/max(len(cf),1):.0f}%)")

print("\n  CFOG случаи с ЕДИНСТВЕН признак (кандидати да отпаднат):")
for r in sorted(cf, key=lambda x: x["vis_min"]):
    if r["n_fogwx"] < 2 and r["n_fogvis"] < 2:
        print(f"    {r['case']:<30} vis_min={r['vis_min']:>6.0f} "
              f"явл={r['n_fogwx']} <1km={r['n_fogvis']} "
              f"<2km={r['n_lowvis']}")

print("\n  НЕ-CFOG случаи с признаци за мъгла (кандидати да влязат):")
for cat in ("CDRY", "CLDY", "DYNM"):
    for r in sorted(by_cat.get(cat, []), key=lambda x: x["vis_min"]):
        if r["n_fogwx"] >= 2 or r["n_fogvis"] >= 2 or r["vis_min"] < 500:
            print(f"    {r['case']:<30} vis_min={r['vis_min']:>6.0f} "
                  f"явл={r['n_fogwx']} <1km={r['n_fogvis']} "
                  f"вятър={r['wind_max']:.0f}k OVC={r['ovc_frac']:.0%}")

if args.dump:
    import csv
    with open(args.dump, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  [CSV] {args.dump}")
