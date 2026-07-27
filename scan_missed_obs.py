# -*- coding: utf-8 -*-
"""
scan_missed_obs.py  —  колко мъгла губи сдвояването на кръгъл час
==================================================================
verify_cases.py, _hourly_pairs(), ред ~368: всеки моделен час се
сдвоява с НАЙ-БЛИЗКИЯ METAR в ±30 мин. При станция, докладваща на
всеки половин час, точният час винаги печели и половинчасовите
наблюдения се изхвърлят системно.

Открито 27.07.2026 по LBPD_CFOG_2025-02-25:
    18:00  3600 m   ← верификацията вижда това
    18:30   250 m   ← и изхвърля това
    19:00  2100 m
Наблюдаван минимум според верификацията: 2000 m. Истински: 250 m.
Случаят е етикетиран CFOG, а получава изход FA.

КАКВО МЕРИ
----------
За всеки случай:
    VIS_точен  — минимум по наблюденията на КРЪГЪЛ час (както сега)
    VIS_всички — минимум по ВСИЧКИ наблюдения в същия прозорец
    пропуснати — часове, в които точният час е над прага, но има
                 наблюдение в ±30 мин под прага

Употреба:
    python scan_missed_obs.py
    python scan_missed_obs.py --thr 2000        (събитийният праг)
    python scan_missed_obs.py --icao LBPD --detail
"""
import argparse, glob, json, os, re
from collections import defaultdict

START_HOUR, FORECAST_H = 18, 15

_WIND = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?(KT|MPS)\b")
_VIS4 = re.compile(r"\b(\d{4})\b")
_TIME = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")
_CAVOK = re.compile(r"\bCAVOK\b")


def strip_trend(line):
    for kw in (" TEMPO ", " BECMG ", " PROB30 ", " PROB40 ", " NOSIG"):
        i = line.find(kw)
        if i > 0:
            line = line[:i]
    return line


def parse(line):
    """Връща (час, минута, видимост) или None."""
    if not line or line.startswith("#"):
        return None
    t = _TIME.search(line)
    if not t:
        return None
    body = strip_trend(line)
    hh, mm = int(t.group(2)), int(t.group(3))
    if _CAVOK.search(body):
        return hh, mm, 10000
    w = _WIND.search(body)
    if not w:
        return None
    for m in _VIS4.finditer(body[w.end():]):
        v = int(m.group(1))
        return hh, mm, (10000 if v == 9999 else v)
    return None


def in_window(hh):
    return hh >= START_HOUR or hh <= (START_HOUR + FORECAST_H) % 24


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icao", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--cases-dir", default="cases")
    ap.add_argument("--thr", type=int, default=1000)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    outcome = {}
    vpath = args.verify
    if not vpath:
        fs = sorted(glob.glob(os.path.join("logs", "verify_*.json")))
        vpath = fs[-1] if fs else None
    if vpath:
        with open(vpath, encoding="utf-8") as f:
            outcome = {r["case_id"]: r["eval"]["event"]
                       for r in json.load(f)["results"] if "error" not in r}
        print(f"[Verify] {os.path.basename(vpath)}")
    print(f"[Праг] VIS < {args.thr} m   [Прозорец] "
          f"{START_HOUR}→{(START_HOUR+FORECAST_H)%24:02d} UTC")
    print()

    rows = []
    for path in sorted(glob.glob(os.path.join(
            args.cases_dir, f"{args.icao or 'LB??'}_*_*.txt"))):
        name = os.path.splitext(os.path.basename(path))[0]
        exact, allobs = {}, defaultdict(list)
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                p = parse(line.strip())
                if not p:
                    continue
                hh, mm, v = p
                if not in_window(hh):
                    continue
                allobs[hh].append((mm, v))
                if mm == 0:
                    exact[hh] = min(exact.get(hh, 99999), v)
        if not allobs:
            continue
        # часове, в които точният час е над прага, но има друго под него
        missed = []
        for hh, lst in allobs.items():
            e = exact.get(hh)
            lo = min(v for _, v in lst)
            if lo < args.thr and (e is None or e >= args.thr):
                missed.append((hh, e, lo))
        v_exact = min(exact.values()) if exact else None
        v_all = min(v for lst in allobs.values() for _, v in lst)
        rows.append((name, outcome.get(name, "?"), v_exact, v_all, missed,
                     sum(len(l) for l in allobs.values()), len(exact)))

    if not rows:
        raise SystemExit("[!] Няма случаи.")

    agg = defaultdict(lambda: [0, 0, 0])
    for name, ev, ve, va, missed, n_all, n_ex in rows:
        a = agg[name[:4]]
        a[0] += 1
        a[1] += bool(missed)
        a[2] += len(missed)

    print(f"{'ICAO':6}{'случаи':>8}{'засегнати':>11}{'проп.часа':>11}")
    print("-" * 36)
    t = [0, 0, 0]
    for ic in sorted(agg):
        a = agg[ic]
        print(f"{ic:6}{a[0]:>8}{a[1]:>11}{a[2]:>11}")
        for i in range(3):
            t[i] += a[i]
    print("-" * 36)
    print(f"{'ОБЩО':6}{t[0]:>8}{t[1]:>11}{t[2]:>11}")

    bad = [r for r in rows if r[4]]
    if bad:
        print()
        print(f"{'случай':30}{'изход':7}{'VIS точен':>10}{'VIS всички':>11}"
              f"{'проп.':>6}  часове (точен→истински)")
        for name, ev, ve, va, missed, _, _ in sorted(
                bad, key=lambda x: x[3]):
            hs = " ".join(f"{h:02d}:{'—' if e is None else e}→{lo}"
                          for h, e, lo in sorted(missed)[:4])
            print(f"{name:30}{ev:7}{(ve if ve else 0):>10}{va:>11}"
                  f"{len(missed):>6}  {hs}")

    n_all = sum(r[5] for r in rows)
    n_ex = sum(r[6] for r in rows)
    print()
    print(f"Наблюдения в прозореца: {n_all} общо, {n_ex} на кръгъл час "
          f"→ {100*(n_all-n_ex)/n_all:.0f} % се изхвърлят")
    print()
    print("Ако 'засегнати' е значимо, наблюдаваният минимум във")
    print("верификацията е системно завишен и част от FA са всъщност HIT.")


if __name__ == "__main__":
    main()
