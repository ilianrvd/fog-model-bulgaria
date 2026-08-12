"""
scan_snow_cases.py
==================
Сканира case файловете и класифицира всеки случай по това КАКВО е
причинило намалената видимост в реалността.

Мотив: `LBSF_CLDY_2024-01-19` се брои като HIT, а реалната ниска
видимост е от снеговалеж при 16 kt вятър (TEMPO SN BLSN), не от мъгла.
Такива случаи изкривяват CSI нагоре.

Класификация на часовете с VIS под прага:
  FOG    — FG / FZFG / MIFG / BCFG в present weather
  MIST   — BR без валеж
  SNOW   — SN / SHSN / BLSN / DRSN / PL / GS
  RAIN   — RA / DZ / FZRA / FZDZ / SHRA
  MIXED  — валеж И мъгла/димка едновременно
  OTHER  — намалена видимост без обяснение в present weather

Употреба:
  python scan_snow_cases.py
  python scan_snow_cases.py --threshold 1000
  python scan_snow_cases.py --event HIT
  python scan_snow_cases.py --csv snow_scan.csv
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# ── Групи present weather ──
FOG_TOK  = ("FZFG", "MIFG", "BCFG", "PRFG", "FG")
MIST_TOK = ("BR",)
SNOW_TOK = ("BLSN", "DRSN", "SHSN", "SNRA", "SG", "PL", "GS", "SN")
RAIN_TOK = ("FZDZ", "FZRA", "SHRA", "DZ", "RA")


def in_night(h):
    return h >= 18 or h <= 7


def parse_obs(raw):
    """Извлича час, видимост и present weather от суров METAR."""
    out = {"hour": None, "minute": None, "vis": None,
           "fog": False, "mist": False, "snow": False, "rain": False,
           "tokens": []}

    m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', raw)
    if not m:
        return None
    out["hour"], out["minute"] = int(m.group(2)), int(m.group(3))

    # Отрязваме TEMPO/BECMG/NOSIG — прогнозна част, не наблюдение
    body = re.split(r'\b(?:TEMPO|BECMG|NOSIG|PROB\d{2})\b', raw)[0]

    # Видимост
    if re.search(r'\bCAVOK\b', body):
        out["vis"] = 10000
    else:
        # Изрязваме частите с 4-цифрени числа, които НЕ са видимост:
        # вятър (вкл. вариация 290V030), RVR (R27/1600U), облачност,
        # QNH. Първото останало 4-цифрено число е видимостта.
        tail = body
        mw = re.search(r'\b(?:VRB|\d{3})\d{2,3}(?:G\d{2,3})?KT'
                       r'(?:\s+\d{3}V\d{3})?', tail)
        if mw:
            tail = tail[mw.end():]
        tail = re.sub(r'\bR\d{2}[LRC]?/[^\s]+', ' ', tail)
        tail = re.sub(r'\bQ\d{4}\b', ' ', tail)
        tail = re.sub(r'\b(?:FEW|SCT|BKN|OVC|VV)\d{3}/*', ' ', tail)
        mv = re.search(r'(?<![\d/])(\d{4})(?![\d/])', tail)
        if mv:
            v = int(mv.group(1))
            out["vis"] = 10000 if v == 9999 else v

    # Present weather — само от тялото, с интензитет
    for tok in FOG_TOK:
        if re.search(r'\b' + tok + r'\b', body):
            out["fog"] = True
            out["tokens"].append(tok)
            break
    for tok in MIST_TOK:
        if re.search(r'\b' + tok + r'\b', body):
            out["mist"] = True
            out["tokens"].append(tok)
            break
    for tok in SNOW_TOK:
        if re.search(r'[-+]?\b' + tok + r'\b', body):
            out["snow"] = True
            out["tokens"].append(tok)
            break
    for tok in RAIN_TOK:
        if re.search(r'[-+]?\b' + tok + r'\b', body):
            out["rain"] = True
            out["tokens"].append(tok)
            break

    return out


def classify_hour(o):
    """Причина за намалената видимост в един час."""
    precip = o["snow"] or o["rain"]
    obsc = o["fog"] or o["mist"]
    if precip and obsc:
        return "MIXED"
    if o["snow"]:
        return "SNOW"
    if o["rain"]:
        return "RAIN"
    if o["fog"]:
        return "FOG"
    if o["mist"]:
        return "MIST"
    return "OTHER"


def scan_case(path, threshold):
    """Връща обобщение за един случай."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f if l.strip().startswith("METAR ")]
    except FileNotFoundError:
        return None

    low_hours = []
    all_night = 0
    for raw in lines:
        o = parse_obs(raw)
        if o is None or o["hour"] is None:
            continue
        if not in_night(o["hour"]):
            continue
        all_night += 1
        if o["vis"] is not None and o["vis"] < threshold:
            low_hours.append({
                "hour": o["hour"], "minute": o["minute"],
                "vis": o["vis"], "cls": classify_hour(o),
                "tokens": o["tokens"], "raw": raw,
            })

    if not low_hours:
        return {"n_low": 0, "classes": Counter(), "verdict": "NO_LOW_VIS",
                "min_vis": None, "low": []}

    cls = Counter(h["cls"] for h in low_hours)
    n = len(low_hours)

    # Присъда за случая
    fogish = cls["FOG"] + cls["MIST"]
    precip = cls["SNOW"] + cls["RAIN"]
    mixed = cls["MIXED"]

    if cls["SNOW"] >= 0.5 * n:
        verdict = "SNOW_DOMINATED"
    elif precip >= 0.5 * n:
        verdict = "PRECIP_DOMINATED"
    elif cls["FOG"] > 0:
        verdict = "REAL_FOG"
    elif mixed >= 0.5 * n:
        verdict = "MIXED"
    elif cls["MIST"] >= 0.5 * n:
        verdict = "MIST_ONLY"
    else:
        verdict = "UNCLEAR"

    return {
        "n_low": n, "classes": cls, "verdict": verdict,
        "min_vis": min(h["vis"] for h in low_hours),
        "low": low_hours,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Класифицира случаите по причина за ниската видимост")
    ap.add_argument("--diag",  default="diagnostic_summary.json")
    ap.add_argument("--cases", default="cases")
    ap.add_argument("--threshold", type=int, default=2000,
                    help="Праг за 'ниска видимост' (по подразбиране 2000 m, "
                         "както EVENT_VIS)")
    ap.add_argument("--event", default=None,
                    help="Само този изход (HIT/MISS/FA/CN)")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--detail", action="store_true",
                    help="Печата и суровите METAR-и на проблемните случаи")
    args = ap.parse_args()

    with open(args.diag, encoding="utf-8") as f:
        diag = json.load(f)

    rows = []
    for rec in diag:
        if args.event and rec.get("event") != args.event:
            continue
        path = os.path.join(args.cases, rec["case"] + ".txt")
        r = scan_case(path, args.threshold)
        if r is None:
            continue
        rows.append({"rec": rec, "scan": r})

    print(f"[INFO] Сканирани {len(rows)} случая, праг VIS < {args.threshold} m")

    # ── Обобщение по изход и присъда ──
    print("\n" + "=" * 74)
    print("  ПРИЧИНА ЗА НИСКАТА ВИДИМОСТ, ПО ИЗХОД")
    print("=" * 74)

    events = ("HIT", "MISS", "FA", "CN")
    verdicts = ("REAL_FOG", "MIXED", "MIST_ONLY", "SNOW_DOMINATED",
                "PRECIP_DOMINATED", "UNCLEAR", "NO_LOW_VIS")

    print(f"  {'изход':<7}" + "".join(f"{v[:9]:>11}" for v in verdicts))
    print("  " + "-" * 72)
    for ev in events:
        grp = [r for r in rows if r["rec"].get("event") == ev]
        if not grp:
            continue
        c = Counter(r["scan"]["verdict"] for r in grp)
        line = f"  {ev:<7}"
        for v in verdicts:
            line += f"{c.get(v, 0):>11}"
        print(line + f"   (n={len(grp)})")

    # ── Проблемните HIT ──
    print("\n" + "=" * 74)
    print("  HIT, ПРИ КОИТО НИСКАТА ВИДИМОСТ НЕ Е ОТ МЪГЛА")
    print("=" * 74)
    bad = [r for r in rows
           if r["rec"].get("event") == "HIT"
           and r["scan"]["verdict"] in ("SNOW_DOMINATED", "PRECIP_DOMINATED")]
    if not bad:
        print("  Няма.")
    else:
        print(f"  {len(bad)} случая:\n")
        for r in bad:
            s, rec = r["scan"], r["rec"]
            cls = " ".join(f"{k}={v}" for k, v in s["classes"].most_common())
            print(f"  {rec['case']:<28} {s['verdict']:<18} "
                  f"minVIS={s['min_vis']}m")
            print(f"    {'':<28} часове: {cls}")
            if args.detail:
                for h in s["low"][:4]:
                    print(f"      {h['hour']:02d}:{h['minute']:02d} "
                          f"{h['vis']:>5}m  {','.join(h['tokens']) or '--'}")
            print()

    # ── Гранични: MIST_ONLY при HIT ──
    print("=" * 74)
    print("  HIT само с BR (димка), без нито един час FG")
    print("=" * 74)
    mist = [r for r in rows
            if r["rec"].get("event") == "HIT"
            and r["scan"]["verdict"] == "MIST_ONLY"]
    if not mist:
        print("  Няма.")
    else:
        print(f"  {len(mist)} случая:\n")
        for r in mist:
            s, rec = r["scan"], r["rec"]
            print(f"  {rec['case']:<28} minVIS={s['min_vis']}m  "
                  f"часове с ниска VIS: {s['n_low']}")

    # ── Ефект върху CSI ──
    print("\n" + "=" * 74)
    print("  ЕФЕКТ ВЪРХУ CSI, АКО СНЕЖНИТЕ СЛУЧАИ СЕ ИЗКЛЮЧАТ")
    print("=" * 74)
    ev_all = Counter(r["rec"].get("event") for r in rows)
    hit, miss, fa = ev_all["HIT"], ev_all["MISS"], ev_all["FA"]
    csi0 = hit / max(hit + miss + fa, 1)

    snow_hit = len([r for r in rows
                    if r["rec"].get("event") == "HIT"
                    and r["scan"]["verdict"] in ("SNOW_DOMINATED",
                                                 "PRECIP_DOMINATED")])
    snow_miss = len([r for r in rows
                     if r["rec"].get("event") == "MISS"
                     and r["scan"]["verdict"] in ("SNOW_DOMINATED",
                                                  "PRECIP_DOMINATED")])
    csi1 = (hit - snow_hit) / max((hit - snow_hit) + (miss - snow_miss) + fa, 1)
    print(f"  Сега:                  HIT={hit} MISS={miss} FA={fa}  "
          f"CSI={csi0:.3f}")
    print(f"  Без валежните случаи:  HIT={hit - snow_hit} "
          f"MISS={miss - snow_miss} FA={fa}  CSI={csi1:.3f}  "
          f"({csi1 - csi0:+.3f})")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case", "icao", "category", "event", "verdict",
                        "n_low_hours", "min_vis_obs", "FOG", "MIST",
                        "SNOW", "RAIN", "MIXED", "OTHER"])
            for r in rows:
                s, rec = r["scan"], r["rec"]
                c = s["classes"]
                w.writerow([rec["case"], rec["icao"], rec.get("category"),
                            rec.get("event"), s["verdict"], s["n_low"],
                            s["min_vis"], c["FOG"], c["MIST"], c["SNOW"],
                            c["RAIN"], c["MIXED"], c["OTHER"]])
        print(f"\n[OK] Записан {args.csv}")


if __name__ == "__main__":
    main()
