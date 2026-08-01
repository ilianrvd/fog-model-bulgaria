# -*- coding: utf-8 -*-
"""
analyse_features.py — търсене на режимен разделител
====================================================
Чете features.csv. Нула пускания на модела.

    python analyse_features.py
    python analyse_features.py --target fa      # само фалшивите аларми
    python analyse_features.py --min-n 15       # по-строг праг за извадка

Два въпроса
-----------
A (--target fog)  Мъглива нощ срещу суха ясна нощ.
    Може ли предварително да се каже коя нощ ще фогне?
    Популации: CFOG срещу CDRY по НАБЛЮДЕНИЕ, не по модел.

B (--target fa)   Сред сухите ясни нощи — кои моделът бърка.
    Популации: CDRY с фалшива аларма срещу CDRY с вярно отхвърляне.
    Ако признак дели тях, това е готов филтър: пусни модела, но
    отхвърли прогнозата за мъгла, когато признакът каже "суха нощ".

Защитата срещу самозаблуда
--------------------------
До всяка точност стои точността на ТРИВИАЛНОТО правило — "винаги
предсказвай мнозинството". Признак с 85 % при мнозинство 80 % не
струва нищо. Класирането е по ПРЕВЕС над тривиалното, не по точност.

Плюс: минимален размер на извадката, и препокриване на диапазоните
(точността при малки извадки почти винаги изглежда обещаващо).
"""
import sys, os, csv, argparse, math
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--target", choices=["fog", "fa", "both"], default="both")
ap.add_argument("--min-n", type=int, default=10,
                help="минимален размер на всяка от двете групи")
ap.add_argument("--top", type=int, default=12)
opt = ap.parse_args()

if not os.path.exists(opt.csv):
    sys.exit(f"Няма {opt.csv}")

rows = list(csv.DictReader(open(opt.csv, encoding="utf-8")))
print(f"  прочетени {len(rows)} реда, {len(rows[0])} колони")

COASTAL = {"LBWN", "LBBG"}
SKIP = {"case", "cat", "date", "event", "icao", "regime",
        "mod_min_vis", "obs_min_vis", "obs_n_fog", "onset", "T_MAE",
        "rh_night_max", "obs_n"}
# КРЪГОВИ признаци — изключени, защото съдържат отговора:
#   mod_min_vis / obs_min_vis / obs_n_fog / onset  — самият изход
#   rh_night_max — при фалшива аларма моделът Е направил мъгла, тоест
#     Е стигнал RH_CRIT=0.995. Даваше 96 % точност и нулево
#     препокриване с праг точно 0.995 — тавтология, не разделител.
#   obs_n — брой наблюдения, няма физически смисъл като признак
#
# rh_early_* НЕ са кръгови: ранната нощ 18–22 UTC предхожда
# мъглообразуването, а праговете излизат около 0.93, далеч под 0.995.


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


FEATURES = [c for c in rows[0] if c not in SKIP]


def best_split(A, B):
    """Най-добър праг и точност за разделяне на A от B."""
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
    A = [x for x in A if x is not None]
    B = [x for x in B if x is not None]
    if not A or not B:
        return None
    lo, hi = max(min(A), min(B)), min(max(A), max(B))
    span = max(max(A), max(B)) - min(min(A), min(B))
    if hi <= lo or span <= 0:
        return 0.0
    return (hi - lo) / span


def analyse(sub, name, posf, negf, lab_pos, lab_neg):
    P = [r for r in sub if posf(r)]
    N = [r for r in sub if negf(r)]
    if len(P) < opt.min_n or len(N) < opt.min_n:
        print(f"\n  {name}: {lab_pos} {len(P)} · {lab_neg} {len(N)}  "
              f"→ ПРОПУСКАМ (под {opt.min_n})")
        return
    majority = max(len(P), len(N)) / (len(P) + len(N))
    print(f"\n{'─'*88}")
    print(f"  {name}   {lab_pos}: {len(P)}   {lab_neg}: {len(N)}   "
          f"тривиално правило: {majority:.0%}")
    print(f"{'─'*88}")
    res = []
    for f in FEATURES:
        A = [num(r.get(f)) for r in P]
        B = [num(r.get(f)) for r in N]
        na = sum(1 for x in A if x is not None)
        nb = sum(1 for x in B if x is not None)
        if na < opt.min_n or nb < opt.min_n:
            continue
        r = best_split(A, B)
        if not r:
            continue
        acc, t, sg = r
        res.append((acc - majority, acc, f, t, sg,
                    float(np.median([x for x in A if x is not None])),
                    float(np.median([x for x in B if x is not None])),
                    overlap(A, B), min(na, nb)))
    if not res:
        print("    няма признак с достатъчно данни")
        return
    res.sort(reverse=True)
    print(f"    {'признак':<20} {'превес':>7} {'точност':>8} "
          f"{lab_pos[:7]:>9} {lab_neg[:7]:>9} {'праг':>9} {'препокр':>8}")
    for lift, acc, f, t, sg, ma, mb, ov, n in res[:opt.top]:
        ovs = "—" if ov is None else f"{ov:.0%}"
        star = " ★" if lift >= 0.10 and (ov or 1) < 0.5 else ""
        print(f"    {f:<20} {lift:>+6.0%} {acc:>8.0%} "
              f"{ma:>9.3f} {mb:>9.3f} {t:>9.3f} {ovs:>8}{star}")
    return res


def is_fog(r):   return r["cat"] == "CFOG"
def is_dry(r):   return r["cat"] == "CDRY"
def is_fa(r):    return r["cat"] == "CDRY" and r["event"] == "FA"
def is_cn(r):    return r["cat"] == "CDRY" and r["event"] == "CN"


TARGETS = []
if opt.target in ("fog", "both"):
    TARGETS.append(("ВЪПРОС A — мъглива нощ срещу суха ясна",
                    is_fog, is_dry, "мъгла", "суха"))
if opt.target in ("fa", "both"):
    TARGETS.append(("ВЪПРОС B — сред сухите: фалшива аларма срещу вярно",
                    is_fa, is_cn, "ФА", "вярно"))

for title, posf, negf, lp, ln in TARGETS:
    print(f"\n{'='*88}")
    print(f"  {title}")
    print(f"{'='*88}")
    analyse(rows, "ВСИЧКИ СТАНЦИИ", posf, negf, lp, ln)
    analyse([r for r in rows if r["icao"] not in COASTAL],
            "КОНТИНЕНТАЛНИ (LBSF, LBGO, LBPD)", posf, negf, lp, ln)
    analyse([r for r in rows if r["icao"] in COASTAL],
            "КРАЙБРЕЖНИ (LBWN, LBBG)", posf, negf, lp, ln)
    for icao in ("LBSF", "LBGO", "LBPD", "LBWN", "LBBG"):
        analyse([r for r in rows if r["icao"] == icao],
                f"САМО {icao}", posf, negf, lp, ln)

print(f"\n{'='*88}")
print("  КАК СЕ ЧЕТЕ")
print(f"{'='*88}")
print("  превес   = точност минус тривиалното правило. Това е числото,")
print("             което има значение. Под +10 % не си струва.")
print("  препокр. = дял общ диапазон. Над 50 % значи, че прагът е")
print("             намерен в шума, дори точността да изглежда добре.")
print("  ★        = превес ≥ 10 % И препокриване < 50 %.")
print()
print("  Извадките са малки. При 20 случая един ред мени точността с 5 %.")
print("  Всичко тук е хипотеза за проверка върху нови случаи.")
