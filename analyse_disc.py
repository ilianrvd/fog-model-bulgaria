# -*- coding: utf-8 -*-
"""
analyse_disc.py — преанализ на probe_disc.json
===============================================
Нула нови пускания. Чете вече събраното и задава ВТОРИЯ въпрос:

  не „кое дели FOG от FA" (знаем: вятър/срез/Ri, ~89 %),
  а „кое дели ПАДНАЛИТЕ от ОЦЕЛЕЛИТЕ CFOG случаи при C2".

    python analyse_disc.py                       # чете probe_disc.json
    python analyse_disc.py --json probe_disc.json --v8 probe_disc_v8.json

Хипотезата на Fable: разделителят е ВЛАЖНОСТНИЯТ ЗАПАС, не турбулентна
величина. LBGO 2024-12-30 оцеля при всички сонди, защото стартира
наситен (T=Td=0.0 °C).

Ако се потвърди, следствието НЕ е влажност в C_H — това би било
протеза. Следствието е, че граничните случаи нямат запас в
кондензацията (17 от 17 опират в RH_CRIT=0.995), и лечението е там.
"""
import sys, os, json, argparse
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--json", default="probe_disc.json",
                help="изходът от probe_discriminate.py С C2 построението")
ap.add_argument("--early", nargs=2, type=float, default=[18.0, 22.0])
ap.add_argument("--start-hour", type=float, default=18.0)
opt = ap.parse_args()

if not os.path.exists(opt.json):
    sys.exit(f"Няма {opt.json}. Пусни probe_discriminate.py първо.")

data = json.load(open(opt.json, encoding="utf-8"))
cases = data["cases"]
h0, h1 = opt.early


def early(c):
    out = []
    for x in c["rec"]:
        h = x["hour"]
        hh = h + 24.0 if h < opt.start_hour - 0.5 else h
        if h0 <= hh <= h1:
            out.append(x)
    return out


def agg(c, key, how="mean"):
    v = [x[key] for x in early(c) if x.get(key) is not None]
    if not v:
        return None
    return float(np.mean(v) if how == "mean" else
                 np.min(v) if how == "min" else np.max(v))


# ── популации
fog = [c for c in cases if c["label"] == "FOG"]
kept = [c for c in fog if c["event"] == "HIT"]
lost = [c for c in fog if c["event"] != "HIT"]
fa = [c for c in cases if c["label"] == "FA"]

print(f"\n{'='*94}")
print(f"  ПОПУЛАЦИИ: оцелели CFOG {len(kept)} · паднали CFOG {len(lost)} · "
      f"FA {len(fa)}")
print(f"{'='*94}")

CAND = [
    ("spread", "мин. T−Td от METAR", lambda c: c.get("spread_min")),
    ("rh_max", "макс. RH ранна нощ", lambda c: agg(c, "rh", "max")),
    ("rh_mean", "средно RH ранна нощ", lambda c: agg(c, "rh")),
    ("qv", "qv приземно", lambda c: agg(c, "qv")),
    ("ql_early", "ql в ранната нощ", lambda c: agg(c, "ql", "max")),
    ("T", "T приземно", lambda c: agg(c, "T")),
    ("dew_defL", "дефицит до насищане [%]",
     lambda c: (lambda r: None if r is None else 100.0 * (0.995 - r))(
         agg(c, "rh", "max"))),
    ("Ri1", "Ri ниво 1", lambda c: agg(c, "Ri1")),
    ("S1", "срез² ниво 1", lambda c: agg(c, "S1")),
    ("U", "приземен вятър", lambda c: agg(c, "U")),
    ("dTsa", "T_skin − T_air", lambda c: agg(c, "dTsa")),
    ("H", "сензибилен поток", lambda c: agg(c, "H")),
    ("cf", "облачност", lambda c: agg(c, "cf")),
]

# ИЗКЛЮЧЕНИ от класирането като кръгови:
#   min_vis — това Е изходът (HIT/MISS се определя от него)
#   ql в ранната нощ — частично кръгов: ако мъглата вече се е образувала
#     до 22 UTC, случаят почти сигурно е HIT. Оставен в таблицата, но
#     маркиран, защото носи и истинска информация (час на образуване).
CIRCULAR = {"ql в ранната нощ"}


def best_split(A, B):
    """Праг и точност за разделяне на A (група 1) от B (група 2)."""
    A = [x for x in A if x is not None]
    B = [x for x in B if x is not None]
    if len(A) < 2 or len(B) < 2:
        return None
    best = (-1.0, None, None)
    for t in sorted(set(A + B)):
        for sg in (+1, -1):
            acc = (sum(1 for x in A if sg * x >= sg * t) +
                   sum(1 for x in B if sg * x < sg * t)) / (len(A) + len(B))
            if acc > best[0]:
                best = (acc, t, sg)
    return best


def overlap(A, B):
    """Дял от диапазона на A, който попада в диапазона на B."""
    A = [x for x in A if x is not None]
    B = [x for x in B if x is not None]
    if not A or not B:
        return None
    lo = max(min(A), min(B))
    hi = min(max(A), max(B))
    if hi <= lo:
        return 0.0
    span = max(max(A), max(B)) - min(min(A), min(B))
    return (hi - lo) / span if span > 0 else 1.0


def table(title, GA, GB, na, nb):
    print(f"\n{'='*94}")
    print(f"  {title}")
    print(f"{'='*94}")
    print(f"  {'признак':<24} {na:>13} {nb:>13} {'праг':>9} "
          f"{'точност':>8} {'препокр.':>9}")
    rows = []
    for key, name, fn in CAND:
        A = [fn(c) for c in GA]
        B = [fn(c) for c in GB]
        r = best_split(A, B)
        if not r:
            continue
        acc, t, sg = r
        ov = overlap(A, B)
        Ac = [x for x in A if x is not None]
        Bc = [x for x in B if x is not None]
        rows.append((acc, name, np.median(Ac), np.median(Bc), t, ov))
    rows.sort(reverse=True)
    for acc, name, ma, mb, t, ov in rows:
        ovs = "—" if ov is None else f"{ov:.0%}"
        mark = "  ⚠кръгов" if name in CIRCULAR else ""
        print(f"  {name:<24} {ma:>13.3f} {mb:>13.3f} {t:>9.3f} "
              f"{acc:>7.0%} {ovs:>9}{mark}")
    return [r for r in rows if r[1] not in CIRCULAR]


r1 = table("ВЪПРОС 2 — ОЦЕЛЕЛИ срещу ПАДНАЛИ CFOG (при C2)",
           kept, lost, "оцелели", "паднали")
r2 = table("за сравнение — FOG срещу FA", fog, fa, "FOG", "FA")

# ── разбор
print(f"\n{'='*94}")
print("  РАЗБОР")
print(f"{'='*94}")
if not r1:
    print("  Недостатъчно данни за въпрос 2.")
else:
    acc, name = r1[0][0], r1[0][1]
    ov = r1[0][5]
    print(f"  Най-добър разделител оцелели/паднали: {name}  ({acc:.0%})")
    print(f"  Препокриване на диапазоните: {'—' if ov is None else f'{ov:.0%}'}")
    wet = {"мин. T−Td от METAR", "макс. RH ранна нощ", "средно RH ранна нощ",
           "qv приземно", "дефицит до насищане [%]"}
    top_wet = [x for x in r1[:3] if x[1] in wet]
    print()
    if acc < 0.80:
        print("  → НИЩО не дели оцелелите от падналите над 80 %.")
        print("    Разграничителят не е локална величина от ранната нощ.")
        print("    Следствие: формулата ще размества граничните случаи")
        print("    произволно. Тогава Етап 3 поправя FA само с цена от")
        print("    CFOG, и въпросът е оперативен — какъв плик на приемане.")
    elif top_wet:
        print(f"  → ВЛАЖНОСТНИЯТ ЗАПАС дели ({top_wet[0][1]}, "
              f"{top_wet[0][0]:.0%}).")
        print("    Хипотезата на Fable се потвърждава. Следствието НЕ е")
        print("    влажност в C_H — това е протеза. Следствието е, че")
        print("    граничните случаи нямат запас в кондензацията.")
        print("    Кондензационният член влиза в групата на Етап 3.")
    else:
        print(f"  → Дели '{name}', но не е влажностен признак.")
        print("    Провери дали е физически смислен като вход на схемата,")
        print("    или е корелация без механизъм при n={}.".format(
            len(kept) + len(lost)))

print(f"\n  ВНИМАНИЕ за размера на извадката: оцелели {len(kept)}, "
      f"паднали {len(lost)}.")
print("  При такива числа един случай мени точността с ~8 %.")
print("  Всеки извод тук е ХИПОТЕЗА за проверка, не установен факт.")

# ── подробности по случай
print(f"\n{'='*94}")
print("  ПО СЛУЧАИ — влажностни признаци")
print(f"{'='*94}")
print(f"  {'случай':<26} {'кл':<9} {'спред':>7} {'RHмакс':>8} {'qv':>7} "
      f"{'ql ран.':>8} {'minVIS':>8}")
for grp, lbl in ((kept, "оцелял"), (lost, "паднал"), (fa, "FA")):
    for c in sorted(grp, key=lambda x: x["case"]):
        sp = c.get("spread_min")
        rh = agg(c, "rh", "max")
        qv = agg(c, "qv")
        ql = agg(c, "ql", "max")
        print(f"  {c['case']:<26} {lbl:<9} "
              f"{'—' if sp is None else f'{sp:7.1f}'} "
              f"{'—' if rh is None else f'{100*rh:7.1f}%'} "
              f"{'—' if qv is None else f'{qv:7.2f}'} "
              f"{'—' if ql is None else f'{ql:8.4f}'} "
              f"{c['min_vis']:>8.0f}")
