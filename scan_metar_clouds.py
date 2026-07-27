# -*- coding: utf-8 -*-
"""
scan_metar_clouds.py  —  облачност и валеж по изход на случая
==============================================================
Отговаря на въпроса: фалшивите тревоги случват ли се в нощи, които
РЕАЛНО са били облачни или с валеж?

Ако да — моделът охлажда под покрито небе, тоест причината е облачната
грешка на ICON (находка №1), а не режимният/вятърният праг.

Чете САМО cases/*.txt и последния verify JSON. Без ICON, без пускове
на модела.

Употреба:
    python scan_metar_clouds.py
    python scan_metar_clouds.py --icao LBPD
    python scan_metar_clouds.py --icao LBPD --verify logs/verify_XXXX.json
    python scan_metar_clouds.py --icao LBPD --detail      (случай по случай)
"""
import argparse, glob, json, os, re
from collections import defaultdict

CASES_DIR = "cases"

# ── METAR парсване ────────────────────────────────────────────────────
_CLOUD = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})(?:///|[A-Z]{2,3})?\b")
_VV    = re.compile(r"\bVV(\d{3}|///)\b")
_CLEAR = re.compile(r"\b(NSC|NCD|SKC|CLR|CAVOK)\b")
_PRECIP = re.compile(
    r"(?<![A-Z])(?:[-+]|VC)?(?:MI|BC|PR|DR|BL|SH|TS|FZ)?"
    r"(RA|DZ|SN|SG|PL|GR|GS|UP)(?![A-Z])")
_OBSC  = re.compile(r"(?<![A-Z])(?:MI|BC|PR)?(FG|BR)(?![A-Z])")
_TIME  = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")

OKTAS = {"FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8}


def strip_trend(line):
    """Реже TEMPO/BECMG/PROB частта — тя е прогноза, не наблюдение."""
    for kw in (" TEMPO ", " BECMG ", " PROB30 ", " PROB40 ", " NOSIG"):
        i = line.find(kw)
        if i > 0:
            line = line[:i]
    return line


def parse(line):
    """Връща dict с наблюдаваните облачност/валеж/мъгла или None."""
    if not line or line.startswith("#"):
        return None
    t = _TIME.search(line)
    if not t:
        return None
    body = strip_trend(line)

    oktas, ceiling = 0, None
    for m in _CLOUD.finditer(body):
        typ, h = m.group(1), int(m.group(2)) * 100
        oktas = max(oktas, OKTAS[typ])
        if typ in ("BKN", "OVC") and (ceiling is None or h < ceiling):
            ceiling = h
    vv = _VV.search(body)
    if vv:
        oktas = 8
        if vv.group(1) != "///":
            ceiling = min(ceiling or 99999, int(vv.group(1)) * 100)
    if _CLEAR.search(body) and not oktas:
        oktas, ceiling = 0, None

    obs = _OBSC.search(body)
    return {
        "hour": int(t.group(2)),
        "oktas": oktas,
        "ceiling": ceiling,
        "precip": bool(_PRECIP.search(body)),
        "fog": obs.group(1) == "FG" if obs else False,
        "mist": obs.group(1) == "BR" if obs else False,
    }


def night_only(rows):
    """Само нощните часове 18-08 UTC — там се решава радиационната мъгла."""
    return [r for r in rows if r["hour"] >= 18 or r["hour"] <= 8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icao", default=None)
    ap.add_argument("--verify", default=None)
    ap.add_argument("--cases-dir", default=CASES_DIR)
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--ceil-thr", type=int, default=3000,
                    help="праг за 'ниска' основа [ft]")
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

    pat = os.path.join(args.cases_dir,
                       f"{args.icao or 'LB??'}_*_*.txt")
    per_case = {}
    for path in sorted(glob.glob(pat)):
        name = os.path.splitext(os.path.basename(path))[0]
        if name not in outcome:
            continue
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                r = parse(line.strip())
                if r:
                    rows.append(r)
        rows = night_only(rows)
        if not rows:
            continue
        n = len(rows)
        per_case[name] = {
            "ev": outcome[name],
            "n": n,
            "okt": sum(r["oktas"] for r in rows) / n,
            "low": sum(1 for r in rows
                       if r["ceiling"] is not None
                       and r["ceiling"] < args.ceil_thr) / n,
            "prec": sum(1 for r in rows if r["precip"]) / n,
            "fog": sum(1 for r in rows if r["fog"]) / n,
            "mist": sum(1 for r in rows if r["mist"]) / n,
        }

    if not per_case:
        raise SystemExit("[!] Няма съвпадащи случаи.")
    print(f"[Случаи] {len(per_case)}   (нощни часове 18-08 UTC)")
    print()

    g = defaultdict(list)
    for name, d in per_case.items():
        g[(name[:4], d["ev"])].append(d)

    hdr = (f"{'ICAO':6}{'изход':7}{'n':>5}{'ср.октас':>10}"
           f"{'% ниска осн.':>14}{'% валеж':>9}{'% FG':>7}{'% BR':>7}")
    print(hdr)
    print("-" * len(hdr))
    for icao in sorted({k[0] for k in g}):
        for ev in ("HIT", "MISS", "FA", "CN"):
            rows = g.get((icao, ev))
            if not rows:
                continue
            m = len(rows)
            print(f"{icao:6}{ev:7}{m:>5}"
                  f"{sum(r['okt'] for r in rows)/m:>10.2f}"
                  f"{100*sum(r['low'] for r in rows)/m:>13.1f}%"
                  f"{100*sum(r['prec'] for r in rows)/m:>8.1f}%"
                  f"{100*sum(r['fog'] for r in rows)/m:>6.1f}%"
                  f"{100*sum(r['mist'] for r in rows)/m:>6.1f}%")
        print("-" * len(hdr))

    if args.detail and args.icao:
        print()
        print(f"Случай по случай — {args.icao}, само FA и HIT:")
        print(f"{'случай':30}{'изход':7}{'октас':>7}{'ниска':>8}{'валеж':>8}")
        for name, d in sorted(per_case.items(),
                              key=lambda x: (-x[1]["okt"],)):
            if d["ev"] in ("FA", "HIT"):
                print(f"{name:30}{d['ev']:7}{d['okt']:>7.2f}"
                      f"{100*d['low']:>7.0f}%{100*d['prec']:>7.0f}%")

    print()
    print("Четене: ако FA имат ЗНАЧИТЕЛНО повече октас / ниска основа /")
    print("валеж от CN, моделът охлажда под реално покрито небе и")
    print("причината е облачната грешка на ICON, не вятърният праг.")


if __name__ == "__main__":
    main()
