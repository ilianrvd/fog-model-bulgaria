# -*- coding: utf-8 -*-
"""
scan_metar_clouds2.py  —  облачност по височина, без кръговост
===============================================================
Отговаря на възражението към scan_metar_clouds.py: високият октас в
FA нощите може да е САМАТА мъгла (VV001, OVC002), а не покривка,
която пречи на радиационното охлаждане. Тогава корелацията е кръгова.

Второ възражение: влажните нощи дават и ниски стратуси, и мъгла —
сигналът може да значи просто "влажна нощ".

КАКВО ПРАВИ
-----------
1) Разделя облачността на три пояса по основа:
      ВИСОКА  >= 2000 ft  — не може да е мъгла; блокира LW охлаждане
      СРЕДНА  500-2000 ft — ниски стратуси, вероятно не мъгла
      НИСКА   < 500 ft или VV — двусмислена, вероятно самата мъгла

2) Опция --clear-hours: брои САМО часовете с наблюдавана видимост
   над праг (по подразбиране 5000 m), тоест часове, в които със
   сигурност няма мъгла. Ако сигналът оцелее там, той не е кръгов.

Изводът за облачната грешка на ICON е валиден САМО ако разликата
FA-срещу-CN остане в пояса ВИСОКА и при --clear-hours.

Употреба:
    python scan_metar_clouds2.py --icao LBPD
    python scan_metar_clouds2.py --icao LBPD --clear-hours
    python scan_metar_clouds2.py --icao LBPD --clear-hours --vis-thr 8000
"""
import argparse, glob, json, os, re
from collections import defaultdict

_CLOUD = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})(?:///|[A-Z]{2,3})?\b")
_VV    = re.compile(r"\bVV(\d{3}|///)\b")
_CLEAR = re.compile(r"\b(NSC|NCD|SKC|CLR|CAVOK)\b")
_PRECIP = re.compile(r"(?<![A-Z])(?:[-+]|VC)?(?:MI|BC|PR|DR|BL|SH|TS|FZ)?"
                     r"(RA|DZ|SN|SG|PL|GR|GS|UP)(?![A-Z])")
_TIME  = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")
_WIND  = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?(KT|MPS)\b")
_VIS4  = re.compile(r"\b(\d{4})\b")

OKTAS = {"FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8}
HIGH_FT, MID_FT = 2000, 500


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

    # видимост: първата 4-цифрена група след вятъра
    vis = None
    if re.search(r"\bCAVOK\b", body):
        vis = 10000
    else:
        w = _WIND.search(body)
        if w:
            for m in _VIS4.finditer(body[w.end():]):
                v = int(m.group(1))
                vis = 10000 if v == 9999 else v
                break

    hi = mid = lo = 0
    for m in _CLOUD.finditer(body):
        typ, h = m.group(1), int(m.group(2)) * 100
        o = OKTAS[typ]
        if h >= HIGH_FT:
            hi = max(hi, o)
        elif h >= MID_FT:
            mid = max(mid, o)
        else:
            lo = max(lo, o)
    if _VV.search(body):
        lo = 8
    if _CLEAR.search(body):
        hi = mid = lo = 0

    return {"hour": int(t.group(2)), "vis": vis,
            "hi": hi, "mid": mid, "lo": lo,
            "precip": bool(_PRECIP.search(body))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icao", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--cases-dir", default="cases")
    ap.add_argument("--clear-hours", action="store_true",
                    help="само часове с наблюдавана видимост над --vis-thr")
    ap.add_argument("--vis-thr", type=int, default=5000)
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args()

    vpath = args.verify
    if not vpath:
        fs = sorted(glob.glob(os.path.join("logs", "verify_*.json")))
        if not fs:
            raise SystemExit("[!] Няма logs/verify_*.json")
        vpath = fs[-1]
    with open(vpath, encoding="utf-8") as f:
        vd = json.load(f)
    outcome = {r["case_id"]: r["eval"]["event"]
               for r in vd["results"] if "error" not in r}

    print(f"[Verify] {os.path.basename(vpath)}")
    print(f"[Пояси]  ВИСОКА >= {HIGH_FT} ft · СРЕДНА {MID_FT}-{HIGH_FT} ft"
          f" · НИСКА < {MID_FT} ft или VV")
    if args.clear_hours:
        print(f"[Филтър] само часове с наблюдавана VIS >= {args.vis_thr} m")
    print()

    per = {}
    for path in sorted(glob.glob(os.path.join(
            args.cases_dir, f"{args.icao or 'LB??'}_*_*.txt"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name not in outcome:
            continue
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                r = parse(line.strip())
                if r and (r["hour"] >= 18 or r["hour"] <= 8):
                    rows.append(r)
        if args.clear_hours:
            rows = [r for r in rows
                    if r["vis"] is not None and r["vis"] >= args.vis_thr]
        if not rows:
            continue
        n = len(rows)
        per[name] = {
            "ev": outcome[name], "n": n,
            "hi": sum(r["hi"] for r in rows) / n,
            "mid": sum(r["mid"] for r in rows) / n,
            "lo": sum(r["lo"] for r in rows) / n,
            "prec": sum(1 for r in rows if r["precip"]) / n,
        }

    if not per:
        raise SystemExit("[!] Няма съвпадащи случаи.")

    g = defaultdict(list)
    for name, d in per.items():
        g[(name[:4], d["ev"])].append(d)

    hdr = (f"{'ICAO':6}{'изход':7}{'сл.':>5}{'ч/сл':>6}"
           f"{'ВИСОКА':>9}{'СРЕДНА':>9}{'НИСКА':>8}{'валеж':>8}")
    print(hdr)
    print("-" * len(hdr))
    for icao in sorted({k[0] for k in g}):
        for ev in ("HIT", "MISS", "FA", "CN"):
            rows = g.get((icao, ev))
            if not rows:
                continue
            m = len(rows)
            print(f"{icao:6}{ev:7}{m:>5}"
                  f"{sum(r['n'] for r in rows)/m:>6.1f}"
                  f"{sum(r['hi'] for r in rows)/m:>9.2f}"
                  f"{sum(r['mid'] for r in rows)/m:>9.2f}"
                  f"{sum(r['lo'] for r in rows)/m:>8.2f}"
                  f"{100*sum(r['prec'] for r in rows)/m:>7.1f}%")
        print("-" * len(hdr))

    if args.detail and args.icao:
        print()
        print(f"{'случай':30}{'изход':7}{'ВИС':>6}{'СР':>6}{'НИС':>6}{'ч':>4}")
        for name, d in sorted(per.items(), key=lambda x: -x[1]["hi"]):
            if d["ev"] in ("FA", "HIT"):
                print(f"{name:30}{d['ev']:7}{d['hi']:>6.2f}"
                      f"{d['mid']:>6.2f}{d['lo']:>6.2f}{d['n']:>4}")

    print()
    print("РЕШАВАЩО: разликата FA-срещу-CN трябва да е в пояс ВИСОКА")
    print("И да оцелее при --clear-hours. Само тогава става дума за")
    print("облачна покривка, а не за самата мъгла или за 'влажна нощ'.")


if __name__ == "__main__":
    main()
