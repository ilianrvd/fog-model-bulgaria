# -*- coding: utf-8 -*-
"""
validate_split.py — проверка с отложена извадка
================================================
Чете features.csv. Нула пускания на модела.

    python validate_split.py
    python validate_split.py --feature rh_early_max --target fa

Защо
----
Праговете от analyse_features.py са изведени и измерени върху ЕДНИ И
СЪЩИ данни. Такава точност е винаги завишена — прагът се нагажда към
конкретните случаи, включително към шума в тях.

Тук прагът се изважда от една част от случаите, а точността се мери
върху друга, която прагът не е виждал. Разликата между двете е
размерът на самозаблудата.

Три проверки
------------
1. Разделяне по ВРЕМЕ — праг от по-старите нощи, проверка върху
   по-новите. Това е най-близко до оперативната употреба: настройваш
   на миналото, ползваш на бъдещето.
2. Кръстосана проверка на пет части, с разбъркване.
3. Проверка на случайност — същото върху разбъркани етикети. Ако
   признакът дава превес и там, методът намира сигнал в шума.
"""
import sys, os, csv, math, random, argparse
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--target", choices=["fog", "fa"], default="fa")
ap.add_argument("--features", nargs="*", default=None)
ap.add_argument("--group", choices=["all", "cont", "coast"], default="cont")
ap.add_argument("--folds", type=int, default=5)
ap.add_argument("--repeats", type=int, default=40)
ap.add_argument("--seed", type=int, default=20260731)
opt = ap.parse_args()

if not os.path.exists(opt.csv):
    sys.exit(f"Няма {opt.csv}")

rows = list(csv.DictReader(open(opt.csv, encoding="utf-8")))
COASTAL = {"LBWN", "LBBG"}

CAND_FA = ["rh_early_max", "rh_early_mean", "T_early_mean", "T_soil",
           "T_min", "prof_T_50", "prof_rh_10", "icon_rh_100", "icon_rh_200"]
CAND_FOG = ["rh_early_mean", "rh_early_max", "prof_rh_10", "icon_rh_50",
            "icon_rh_300", "icon_T_50", "cf_mean", "prof_rh_50"]
FEATURES = opt.features or (CAND_FA if opt.target == "fa" else CAND_FOG)


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


if opt.target == "fa":
    pos = lambda r: r["cat"] == "CDRY" and r["event"] == "FA"
    neg = lambda r: r["cat"] == "CDRY" and r["event"] == "CN"
    LP, LN = "фалшива аларма", "вярно отхвърляне"
else:
    pos = lambda r: r["cat"] == "CFOG"
    neg = lambda r: r["cat"] == "CDRY"
    LP, LN = "мъглива нощ", "суха ясна нощ"

sub = rows
if opt.group == "cont":
    sub = [r for r in rows if r["icao"] not in COASTAL]
elif opt.group == "coast":
    sub = [r for r in rows if r["icao"] in COASTAL]

data = [(r, 1) for r in sub if pos(r)] + [(r, 0) for r in sub if neg(r)]
n_pos = sum(1 for _, y in data if y == 1)
n_neg = len(data) - n_pos
print(f"\n{'='*82}")
print(f"  ПРОВЕРКА С ОТЛОЖЕНА ИЗВАДКА")
print(f"  група: {opt.group}   {LP}: {n_pos}   {LN}: {n_neg}")
print(f"{'='*82}")
if n_pos < 8 or n_neg < 8:
    sys.exit("  Извадката е твърде малка за разделяне.")


def fit(train, f):
    """Изважда праг и посока от обучаващата част."""
    A = [num(r.get(f)) for r, y in train if y == 1]
    B = [num(r.get(f)) for r, y in train if y == 0]
    A = [x for x in A if x is not None]
    B = [x for x in B if x is not None]
    if len(A) < 3 or len(B) < 3:
        return None
    best = (-1.0, None, None)
    for t in sorted(set(A + B)):
        for sg in (+1, -1):
            acc = (sum(1 for x in A if sg * x >= sg * t) +
                   sum(1 for x in B if sg * x < sg * t)) / (len(A) + len(B))
            if acc > best[0]:
                best = (acc, t, sg)
    return best[1], best[2], best[0]


def score(test, f, t, sg):
    ok = n = 0
    for r, y in test:
        v = num(r.get(f))
        if v is None:
            continue
        pred = 1 if sg * v >= sg * t else 0
        ok += (pred == y)
        n += 1
    return (ok / n if n else float("nan")), n


def majority(part):
    if not part:
        return float("nan")
    p = sum(1 for _, y in part if y == 1)
    return max(p, len(part) - p) / len(part)


# ── 1. по време
print(f"\n  1. РАЗДЕЛЯНЕ ПО ВРЕМЕ  (праг от по-старите, проверка на по-новите)")
byt = sorted(data, key=lambda rv: rv[0]["date"])
cut = int(len(byt) * 0.6)
tr, te = byt[:cut], byt[cut:]
print(f"     обучение {len(tr)} нощи до {tr[-1][0]['date']}, "
      f"проверка {len(te)} нощи от {te[0][0]['date']}")
print(f"     тривиално върху проверката: {majority(te):.0%}")
print(f"     {'признак':<18} {'праг':>9} {'обучение':>9} {'проверка':>9} "
      f"{'превес':>7}")
rows_t = []
for f in FEATURES:
    r = fit(tr, f)
    if not r:
        continue
    t, sg, acc_tr = r
    acc_te, n = score(te, f, t, sg)
    if n < 5:
        continue
    rows_t.append((acc_te - majority(te), f, t, acc_tr, acc_te))
rows_t.sort(reverse=True)
for lift, f, t, a1, a2 in rows_t:
    print(f"     {f:<18} {t:>9.3f} {a1:>8.0%} {a2:>9.0%} {lift:>+7.0%}")

# ── 2. кръстосана проверка
print(f"\n  2. КРЪСТОСАНА ПРОВЕРКА  ({opt.folds} части × {opt.repeats} повторения)")
print(f"     {'признак':<18} {'превес ср.':>11} {'разсейване':>11} "
       f"{'най-лош':>9}")
rng = random.Random(opt.seed)
cv = {}
for f in FEATURES:
    lifts = []
    for _ in range(opt.repeats):
        d = data[:]
        rng.shuffle(d)
        k = opt.folds
        for i in range(k):
            te2 = d[i::k]
            tr2 = [x for j, x in enumerate(d) if j % k != i]
            r = fit(tr2, f)
            if not r:
                continue
            t, sg, _ = r
            a, n = score(te2, f, t, sg)
            if n >= 5 and not math.isnan(a):
                lifts.append(a - majority(te2))
    if lifts:
        cv[f] = lifts
for f, l in sorted(cv.items(), key=lambda kv: -float(np.mean(kv[1]))):
    print(f"     {f:<18} {np.mean(l):>+10.1%} {np.std(l):>10.1%} "
          f"{np.percentile(l, 10):>+9.1%}")

# ── 3. разбъркани етикети
print(f"\n  3. ПРОВЕРКА НА СЛУЧАЙНОСТ  (същото върху разбъркани етикети)")
print(f"     Показва какъв превес методът намира в чист шум.")
print(f"     {'признак':<18} {'истински':>10} {'разбъркан':>11} {'разлика':>9}")
for f in sorted(cv, key=lambda k: -float(np.mean(cv[k])))[:6]:
    sh = []
    for _ in range(opt.repeats):
        d = [(r, y) for r, y in data]
        ys = [y for _, y in d]
        rng.shuffle(ys)
        d = [(r, y) for (r, _), y in zip(d, ys)]
        rng.shuffle(d)
        for i in range(opt.folds):
            te2 = d[i::opt.folds]
            tr2 = [x for j, x in enumerate(d) if j % opt.folds != i]
            r2 = fit(tr2, f)
            if not r2:
                continue
            t, sg, _ = r2
            a, n = score(te2, f, t, sg)
            if n >= 5 and not math.isnan(a):
                sh.append(a - majority(te2))
    real = float(np.mean(cv[f]))
    fake = float(np.mean(sh)) if sh else float("nan")
    print(f"     {f:<18} {real:>+9.1%} {fake:>+10.1%} {real-fake:>+9.1%}")

print(f"\n{'='*82}")
print("  КАК СЕ ЧЕТЕ")
print(f"{'='*82}")
print("  Разликата между обучение и проверка е размерът на самозаблудата.")
print("  Превес под +10 % при кръстосаната проверка не си струва.")
print("  Ако разбърканите етикети дават +5 % и повече, методът намира")
print("  сигнал в шума и всичко под тази стойност е безсмислено.")
print("  Разсейването показва колко зависи резултатът от коя част се пада")
print("  за проверка — голямо разсейване значи крехък праг.")
