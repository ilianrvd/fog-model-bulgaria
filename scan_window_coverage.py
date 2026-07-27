# -*- coding: utf-8 -*-
"""
scan_window_coverage.py  —  мъгла извън оценявания прозорец
============================================================
Проверява дали наблюдаваната мъгла попада в периода, за който моделът
изобщо е питан.

ТРИ ПРОЗОРЕЦА
-------------
    досие          — всичко в cases/*.txt (теглено до +20h, тоест 18→14 UTC)
    часова метрика — 18 → 09 UTC   (START_HOUR + FORECAST_H = 18+15)
    събитийна      — 18 → 07 UTC   (EVENT_END_UTC = 7 реже часове 8..15)

ЗАЩО
----
Открито 27.07.2026: четири случая, етикетирани CFOG, излизат с изход FA
и h_miss = 0 — тоест наблюдаваната видимост НИКОГА не пада под 1000 m
в оценявания прозорец, въпреки че в досието има мъгла.

Пример LBPD_CFOG_2025-02-25: find_dense_fog дава мин. 250 m и един час
под 1000 m, а верификацията показва 2000–7000 m през целия прозорец.
Мъглата е след 09 UTC — извън хоризонта. Случаят е подбран заради
събитие, което моделът никога не е бил питан да прогнозира, и се
наказва като фалшива тревога.

КАКВО ПРАВИ
-----------
За всеки случай брои часовете с VIS под праг в трите прозореца и
маркира несъответствията:

    ИЗВЪН   мъгла има в досието, но не и в часовия прозорец
    КАРАНТ  мъгла има в часовия, но не и в събитийния (08-09 UTC)

Употреба:
    python scan_window_coverage.py
    python scan_window_coverage.py --icao LBPD --detail
    python scan_window_coverage.py --thr 2000        (събитийният праг)
"""
import argparse, glob, json, os, re
from collections import defaultdict

START_HOUR, FORECAST_H, EVENT_END_UTC = 18, 15, 7

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
    if not line or line.startswith("#"):
        return None
    t = _TIME.search(line)
    if not t:
        return None
    body = strip_trend(line)
    if _CAVOK.search(body):
        return int(t.group(2)), 10000
    w = _WIND.search(body)
    if not w:
        return None
    for m in _VIS4.finditer(body[w.end():]):
        v = int(m.group(1))
        return int(t.group(2)), (10000 if v == 9999 else v)
    return None


def in_hourly(hh):
    end = (START_HOUR + FORECAST_H) % 24        # 9
    return hh >= START_HOUR or hh <= end


def in_event(hh):
    return not (EVENT_END_UTC < hh < 16)


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
    print(f"[Прозорци] досие=всичко · часова={START_HOUR}→"
          f"{(START_HOUR+FORECAST_H)%24:02d} · събитийна={START_HOUR}→"
          f"{EVENT_END_UTC:02d} UTC")
    print(f"[Праг] VIS < {args.thr} m")
    print()

    rows = []
    for path in sorted(glob.glob(os.path.join(
            args.cases_dir, f"{args.icao or 'LB??'}_*_*.txt"))):
        name = os.path.splitext(os.path.basename(path))[0]
        seen = {}
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                p = parse(line.strip())
                if p:
                    hh, v = p
                    seen[hh] = min(seen.get(hh, 99999), v)
        if not seen:
            continue
        tot = sum(1 for h, v in seen.items() if v < args.thr)
        hrl = sum(1 for h, v in seen.items() if v < args.thr and in_hourly(h))
        evt = sum(1 for h, v in seen.items()
                  if v < args.thr and in_hourly(h) and in_event(h))
        flag = ""
        if tot and not hrl:
            flag = "ИЗВЪН"
        elif hrl and not evt:
            flag = "КАРАНТ"
        rows.append((name, outcome.get(name, "?"), tot, hrl, evt, flag,
                     sorted(h for h, v in seen.items() if v < args.thr)))

    if not rows:
        raise SystemExit("[!] Няма случаи.")

    agg = defaultdict(lambda: [0, 0, 0])
    for name, ev, tot, hrl, evt, flag, _ in rows:
        a = agg[name[:4]]
        a[0] += 1
        a[1] += (flag == "ИЗВЪН")
        a[2] += (flag == "КАРАНТ")

    print(f"{'ICAO':6}{'случаи':>8}{'ИЗВЪН':>8}{'КАРАНТ':>9}")
    print("-" * 31)
    t = [0, 0, 0]
    for ic in sorted(agg):
        a = agg[ic]
        print(f"{ic:6}{a[0]:>8}{a[1]:>8}{a[2]:>9}")
        for i in range(3):
            t[i] += a[i]
    print("-" * 31)
    print(f"{'ОБЩО':6}{t[0]:>8}{t[1]:>8}{t[2]:>9}")

    prob = [r for r in rows if r[5]]
    if prob:
        print()
        print(f"{'случай':30}{'изход':7}{'досие':>7}{'часова':>8}"
              f"{'съб.':>6}{'флаг':>8}  часове с мъгла")
        for name, ev, tot, hrl, evt, flag, hrs in sorted(
                prob, key=lambda x: (x[5], x[0])):
            hs = ",".join(f"{h:02d}" for h in hrs[:10])
            print(f"{name:30}{ev:7}{tot:>7}{hrl:>8}{evt:>6}{flag:>8}  {hs}")

    if args.detail:
        print()
        print("Всички случаи с мъгла в досието:")
        for name, ev, tot, hrl, evt, flag, hrs in rows:
            if tot:
                print(f"  {name:30}{ev:7}{tot:>4}{hrl:>4}{evt:>4}  {flag}")

    print()
    print("ИЗВЪН  = мъгла в досието, но не и в часовия прозорец (след 09 UTC)")
    print("КАРАНТ = мъгла в часовия, но не и в събитийния (08-09 UTC)")


if __name__ == "__main__":
    main()
