# -*- coding: utf-8 -*-
"""
combine_features.py — разделител от ДВЕ величини
=================================================
Чете features.csv. Нула пускания на модела.

    python combine_features.py
    python combine_features.py --group all --repeats 60

Въпросът
--------
Досега мерихме по един признак наведнъж. Резултатът: приземната
влажност в 18–22 UTC дели сухите нощи от влажните, но НЕ дели
мъглените от фалшивите — медиани 0.992 срещу 0.981, практически
еднакви.

Тук се търси разделител в ДВЕ измерения. Популацията е точно тази,
която има значение: случаите, в които моделът е обявил мъгла.
    HIT  — обявил и е било
    FA   — обявил и не е било
Ако нещо ги дели, това е готов филтър върху прогнозата.

Метод
-----
Fisher-ова посока за всяка двойка признаци: направлението се смята
аналитично от данните, не се търси с решетка. Това е ВАЖНО при малки
извадки — изчерпателното търсене по две прагови стойности има толкова
свобода, че намира разделение и в чист шум.

Всяка двойка минава:
  1. кръстосана проверка на 5 части × N повторения
  2. същото върху разбъркани етикети (колко дава методът в шум)
Класирането е по превес НАД разбърканото, не по точност.
"""
import sys, os, csv, math, random, argparse, itertools
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--group", choices=["all", "cont", "coast"], default="cont")
ap.add_argument("--folds", type=int, default=5)
ap.add_argument("--repeats", type=int, default=30)
ap.add_argument("--top", type=int, default=15)
ap.add_argument("--max-features", type=int, default=16)
ap.add_argument("--seed", type=int, default=20260731)
opt = ap.parse_args()

rows = list(csv.DictReader(open(opt.csv, encoding="utf-8")))
COASTAL = {"LBWN", "LBBG"}
SKIP = {"case", "cat", "date", "event", "icao", "regime", "mod_min_vis",
        "obs_min_vis", "obs_n_fog", "onset", "T_MAE", "rh_night_max", "obs_n"}


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


sub = rows
if opt.group == "cont":
    sub = [r for r in rows if r["icao"] not in COASTAL]
elif opt.group == "coast":
    sub = [r for r in rows if r["icao"] in COASTAL]

# ПОПУЛАЦИЯТА: случаите, в които моделът е обявил мъгла
P = [r for r in sub if r["event"] == "HIT"]
N = [r for r in sub if r["event"] == "FA"]
print(f"\n{'='*86}")
print(f"  РАЗДЕЛИТЕЛ ОТ ДВЕ ВЕЛИЧИНИ   група: {opt.group}")
print(f"  Популация: случаите, в които моделът обявява мъгла")
print(f"    HIT (обявил и е било) : {len(P)}")
print(f"    FA  (обявил, не е)    : {len(N)}")
maj = max(len(P), len(N)) / (len(P) + len(N))
print(f"    тривиално правило     : {maj:.0%}")
print(f"{'='*86}")
if len(P) < 12 or len(N) < 12:
    sys.exit("  Извадката е твърде малка.")

# признаци с достатъчно попълненост
cands = []
for f in rows[0]:
    if f in SKIP:
        continue
    a = [num(r.get(f)) for r in P]
    b = [num(r.get(f)) for r in N]
    if sum(x is not None for x in a) >= 0.9 * len(P) and \
       sum(x is not None for x in b) >= 0.9 * len(N):
        cands.append(f)

# отсяваме по единичен превес, за да не комбинираме шум с шум
def single_lift(f):
    a = [x for x in (num(r.get(f)) for r in P) if x is not None]
    b = [x for x in (num(r.get(f)) for r in N) if x is not None]
    best = 0.0
    for t in sorted(set(a + b)):
        for sg in (+1, -1):
            acc = (sum(1 for x in a if sg * x >= sg * t) +
                   sum(1 for x in b if sg * x < sg * t)) / (len(a) + len(b))
            best = max(best, acc)
    return best - maj


ranked = sorted(((single_lift(f), f) for f in cands), reverse=True)
FEAT = [f for _, f in ranked[:opt.max_features]]
print(f"\n  Единично (върху всички данни, завишено):")
for lift, f in ranked[:8]:
    print(f"    {f:<20} {lift:>+6.0%}")
print(f"\n  Комбинират се {len(FEAT)} признака → "
      f"{len(FEAT)*(len(FEAT)-1)//2} двойки")

# ── данни като матрица
def mat(fs, rs):
    out = []
    for r in rs:
        v = [num(r.get(f)) for f in fs]
        out.append(v)
    return out


def fisher_fit(X, y):
    """Fisher-ова посока + праг върху проекцията. X: списък двойки."""
    A = np.array([x for x, yy in zip(X, y) if yy == 1 and None not in x],
                 dtype=float)
    B = np.array([x for x, yy in zip(X, y) if yy == 0 and None not in x],
                 dtype=float)
    if len(A) < 3 or len(B) < 3:
        return None
    mu = (A.mean(0) + B.mean(0)) / 2.0
    sd = np.concatenate([A, B]).std(0)
    sd[sd < 1e-12] = 1.0
    A_ = (A - mu) / sd
    B_ = (B - mu) / sd
    Sw = np.cov(A_.T) * (len(A_) - 1) + np.cov(B_.T) * (len(B_) - 1)
    Sw = np.atleast_2d(Sw) + np.eye(A_.shape[1]) * 1e-6
    try:
        w = np.linalg.solve(Sw, A_.mean(0) - B_.mean(0))
    except np.linalg.LinAlgError:
        return None
    pa, pb = A_ @ w, B_ @ w
    best = (-1.0, None)
    for t in sorted(set(np.concatenate([pa, pb]).tolist())):
        acc = (np.sum(pa >= t) + np.sum(pb < t)) / (len(pa) + len(pb))
        if acc > best[0]:
            best = (acc, t)
    return dict(w=w, t=best[1], mu=mu, sd=sd, acc_train=best[0])


def fisher_score(m, X, y):
    ok = n = 0
    for x, yy in zip(X, y):
        if None in x:
            continue
        p = float(((np.array(x, dtype=float) - m["mu"]) / m["sd"]) @ m["w"])
        ok += ((1 if p >= m["t"] else 0) == yy)
        n += 1
    return (ok / n if n else float("nan")), n


def majority_of(y):
    if not y:
        return float("nan")
    p = sum(y)
    return max(p, len(y) - p) / len(y)


def cv_lift(fs, shuffle=False, rng=None):
    Xp, Xn = mat(fs, P), mat(fs, N)
    X = Xp + Xn
    y = [1] * len(Xp) + [0] * len(Xn)
    if shuffle:
        y = y[:]
        rng.shuffle(y)
    idx = list(range(len(X)))
    lifts = []
    for _ in range(opt.repeats):
        rng.shuffle(idx)
        for i in range(opt.folds):
            te = idx[i::opt.folds]
            tr = [j for k, j in enumerate(idx) if k % opt.folds != i]
            m = fisher_fit([X[j] for j in tr], [y[j] for j in tr])
            if not m:
                continue
            a, n = fisher_score(m, [X[j] for j in te], [y[j] for j in te])
            if n >= 5 and not math.isnan(a):
                lifts.append(a - majority_of([y[j] for j in te]))
    return lifts


rng = random.Random(opt.seed)
res = []
for f1, f2 in itertools.combinations(FEAT, 2):
    l = cv_lift([f1, f2], rng=rng)
    if len(l) < 20:
        continue
    res.append((float(np.mean(l)), float(np.std(l)), f1, f2))
res.sort(reverse=True)

print(f"\n{'='*86}")
print(f"  ДВОЙКИ — кръстосана проверка ({opt.folds} части × {opt.repeats} повт.)")
print(f"{'='*86}")
print(f"    {'признак 1':<20} {'признак 2':<20} {'превес':>8} {'разсейв':>8}")
for mu, sd, f1, f2 in res[:opt.top]:
    print(f"    {f1:<20} {f2:<20} {mu:>+7.1%} {sd:>8.1%}")

print(f"\n{'='*86}")
print(f"  ПРОВЕРКА НА СЛУЧАЙНОСТ — най-добрите {min(6, len(res))} двойки")
print(f"{'='*86}")
print(f"    {'двойка':<42} {'истински':>9} {'разбъркан':>10} {'разлика':>9}")
verdict = []
for mu, sd, f1, f2 in res[:6]:
    sh = cv_lift([f1, f2], shuffle=True, rng=rng)
    fake = float(np.mean(sh)) if sh else float("nan")
    verdict.append((mu - fake, mu, fake, f1, f2))
    print(f"    {f1 + ' + ' + f2:<42} {mu:>+8.1%} {fake:>+9.1%} "
          f"{mu - fake:>+9.1%}")

print(f"\n{'='*86}")
best_single = ranked[0][0] if ranked else 0.0
if verdict:
    d, mu, fake, f1, f2 = max(verdict)
    print(f"  Най-добра двойка: {f1} + {f2}")
    print(f"    превес при проверка : {mu:+.1%}")
    print(f"    същото в чист шум   : {fake:+.1%}")
    print(f"    най-добър единичен  : {best_single:+.1%} (завишен, без проверка)")
    print()
    if mu < 0.10:
        print("  → Комбинацията НЕ дели HIT от FA над 10 % превес.")
        print("    Двете популации не се различават по нито една двойка")
        print("    от наличните величини. Информацията, която дели")
        print("    реалната мъгла от фалшивата, не е в тези данни.")
    elif mu - fake < 0.10:
        print("  → Превесът е сравним с това, което методът намира в шум.")
        print("    Не е надежден резултат при тази извадка.")
    else:
        print("  → Има реален двумерен сигнал. Следва проверка на цената")
        print("    с apply_rule върху пълния набор.")
print(f"{'='*86}")
