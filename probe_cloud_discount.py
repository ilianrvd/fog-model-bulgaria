"""
probe_cloud_discount.py
=======================
Диагностика на облачния дисконт `_lo *= 0.2` при RH>95%.

Сравнява за даден случай:
  - ICON ниска/средна/висока облачност по час  (icon_cache)
  - ICON RH2m по час                            (icon_cache)
  - реалната облачна база и видимост от METAR   (cases/*.txt)
  - ефекта на дисконта върху LW надолу

Нула пускания на модела — само чете файлове.

Употреба:
  python probe_cloud_discount.py LBSF_CLDY_2024-12-10
  python probe_cloud_discount.py LBSF_CLDY_2024-12-10 LBSF_CLDY_2025-01-21
  python probe_cloud_discount.py --all-cldy-fa --diag diagnostic_summary.json

Опции:
  --cases   директория с case файлове   (по подразбиране: cases)
  --cache   директория с icon_cache     (по подразбиране: icon_cache)
  --hour    начален UTC час             (по подразбиране: 18)
  --fh      forecast hours              (по подразбиране: 16)
"""

import argparse
import json
import os
import re
import sys

# Windows: при пренасочване към файл stdout пада на cp1252 и кирилицата
# гърми. Форсираме UTF-8. (Python 3.7+)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


SIG = 5.67e-8          # Стефан-Болцман
EPS_CLEAR = 0.75       # типична емисивност на ясно зимно небе (Prata)


# ──────────────────────────────────────────────────────────────
# METAR парсинг: облачна база, видимост, T/Td, вятър, present wx
# ──────────────────────────────────────────────────────────────

def parse_metar_clouds(raw: str) -> dict:
    """
    Извлича от суров METAR:
      hour, minute, vis_m, T, Td, wind_kt, wind_dir,
      base_lowest_ft  (най-ниската BKN/OVC база, None ако няма),
      base_any_ft     (най-ниската FEW/SCT/BKN/OVC база),
      cover_lowest    ('FEW'/'SCT'/'BKN'/'OVC'/'VV'/None),
      wx              (present weather токени)
    """
    out = {"hour": None, "minute": None, "vis_m": None,
           "T": None, "Td": None, "wind_kt": None, "wind_dir": None,
           "base_lowest_ft": None, "base_any_ft": None,
           "cover_lowest": None, "wx": []}

    m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', raw)
    if m:
        out["hour"]   = int(m.group(2))
        out["minute"] = int(m.group(3))

    # Вятър
    m = re.search(r'\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b', raw)
    if m:
        d = m.group(1)
        out["wind_dir"] = None if d == "VRB" else int(d)
        out["wind_kt"]  = int(m.group(2))

    # Видимост: 4 цифри самостоятелно (9999 = 10 km+), или CAVOK
    if re.search(r'\bCAVOK\b', raw):
        out["vis_m"] = 10000
    else:
        m = re.search(r'\b(\d{4})\b(?!/)', raw.split("Q")[0])
        if m:
            v = int(m.group(1))
            # изключваме времевата група (вече изядена от Z) и QNH
            out["vis_m"] = 10000 if v == 9999 else v

    # T/Td: 'MM/MM' или 'M03/M05'
    m = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
    if m:
        def _t(s):
            return -int(s[1:]) if s.startswith("M") else int(s)
        out["T"]  = _t(m.group(1))
        out["Td"] = _t(m.group(2))

    # Облачни слоеве
    bases_any  = []
    bases_bkn  = []
    cover_low  = None
    low_alt    = None
    for mm in re.finditer(r'\b(FEW|SCT|BKN|OVC)(\d{3})', raw):
        cov = mm.group(1)
        ft  = int(mm.group(2)) * 100
        bases_any.append(ft)
        if cov in ("BKN", "OVC"):
            bases_bkn.append(ft)
        if low_alt is None or ft < low_alt:
            low_alt   = ft
            cover_low = cov

    # Вертикална видимост VV###
    mv = re.search(r'\bVV(\d{3})\b', raw)
    if mv:
        ft = int(mv.group(1)) * 100
        bases_any.append(ft)
        bases_bkn.append(ft)
        if low_alt is None or ft < low_alt:
            low_alt   = ft
            cover_low = "VV"

    out["base_any_ft"]    = min(bases_any) if bases_any else None
    out["base_lowest_ft"] = min(bases_bkn) if bases_bkn else None
    out["cover_lowest"]   = cover_low

    # Present weather (мъгла, дъжд, сняг...)
    for tok in re.findall(r'\b(-|\+)?(FG|BR|RA|SN|DZ|SHRA|SHSN|MIFG|BCFG|HZ)\b', raw):
        out["wx"].append("".join(t for t in tok if t))

    return out


def read_case(path: str) -> list:
    obs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("METAR "):
                continue
            p = parse_metar_clouds(line)
            if p["hour"] is not None:
                p["raw"] = line
                obs.append(p)
    return obs


# ──────────────────────────────────────────────────────────────
# Помощни
# ──────────────────────────────────────────────────────────────

def cf_eff(lo, mi, hi):
    """Ефективна облачност, както я смята моделът."""
    return 1.0 - (1.0 - lo) * (1.0 - 0.7 * mi) * (1.0 - 0.25 * hi)


def in_night(h):
    return h >= 18 or h <= 7


def ft_to_m(ft):
    return None if ft is None else ft * 0.3048


# ──────────────────────────────────────────────────────────────
# Ядро
# ──────────────────────────────────────────────────────────────

def probe(case_id, cases_dir, cache_dir, hour0, fh):
    # Разбиваме case_id: LBSF_CLDY_2024-12-10
    parts = case_id.split("_")
    if len(parts) < 3:
        print(f"[ГРЕШКА] Неразпознат case_id: {case_id}")
        return
    icao = parts[0]
    date = parts[-1]

    cpath = os.path.join(cases_dir, case_id + ".txt")
    ipath = os.path.join(cache_dir, f"{icao}_{date}_{hour0:02d}_{fh}.json")

    if not os.path.exists(cpath):
        print(f"[ГРЕШКА] Няма case файл: {cpath}")
        return
    if not os.path.exists(ipath):
        print(f"[ГРЕШКА] Няма icon_cache: {ipath}")
        return

    obs = read_case(cpath)
    with open(ipath, encoding="utf-8") as f:
        icon = json.load(f)
    cc = icon.get("cc_series", [])

    print("=" * 78)
    print(f"  {case_id}")
    print("=" * 78)

    # ── Таблица час по час ──
    print()
    print("  ICON (от cache)                    │ РЕАЛНО (METAR)")
    print(f"  {'час':>4} {'lo':>5} {'mid':>5} {'hi':>5} {'cf':>5} {'RH2m':>5} {'вал':>5} │ "
          f"{'VIS':>6} {'база':>7} {'покр':>5} {'T/Td':>8} {'wx':>6}")
    print("  " + "-" * 74)

    rows_for_stats = []

    for i, row in enumerate(cc):
        h = (hour0 + i) % 24
        lo, mi, hi = row[0], row[1], row[2]
        rh2 = row[3] if len(row) > 3 else None
        pr  = row[4] if len(row) > 4 else 0.0
        cf  = cf_eff(lo, mi, hi)

        # Най-близкото наблюдение за този час (точен час, после ±30 min)
        best = None
        for o in obs:
            if o["hour"] == h and o["minute"] == 0:
                best = o
                break
        if best is None:
            for o in obs:
                if o["hour"] == h:
                    best = o
                    break

        if best:
            vis = best["vis_m"]
            vis_s = f"{vis}" if vis is not None else "--"
            b = best["base_lowest_ft"] or best["base_any_ft"]
            bm = ft_to_m(b)
            base_s = f"{bm:.0f}m" if bm is not None else "ясно"
            cov_s = best["cover_lowest"] or "--"
            t_s = (f"{best['T']}/{best['Td']}"
                   if best["T"] is not None else "--")
            wx_s = ",".join(best["wx"])[:6] or "--"
        else:
            vis_s = base_s = cov_s = t_s = wx_s = "--"
            bm = None
            vis = None

        night = "*" if in_night(h) else " "
        print(f" {night}{h:4d} {lo:5.2f} {mi:5.2f} {hi:5.2f} {cf:5.2f} "
              f"{(rh2 if rh2 is not None else 0):5.2f} {pr:5.2f} │ "
              f"{vis_s:>6} {base_s:>7} {cov_s:>5} {t_s:>8} {wx_s:>6}")

        if in_night(h):
            rows_for_stats.append({
                "h": h, "lo": lo, "mi": mi, "hi": hi, "cf": cf,
                "rh2": rh2, "pr": pr, "base_m": bm, "vis": vis,
            })

    # ── Ефект на дисконта ──
    print()
    print("  ЕФЕКТ НА ДИСКОНТА  _lo *= 0.2  (при моделна RH0 > 95%)")
    print(f"  {'час':>4} {'cf_без':>7} {'cf_с':>6} {'ΔLW↓ W/m²':>10}")
    print("  " + "-" * 32)
    tot = 0.0
    n = 0
    for r in rows_for_stats:
        cf_ok  = cf_eff(r["lo"], r["mi"], r["hi"])
        cf_dis = cf_eff(r["lo"] * 0.2, r["mi"], r["hi"])
        e_ok  = cf_ok  + (1 - cf_ok)  * EPS_CLEAR
        e_dis = cf_dis + (1 - cf_dis) * EPS_CLEAR
        # референтна T за оценка на потока
        dLW = (e_ok - e_dis) * SIG * 273.0 ** 4
        tot += dLW
        n += 1
        print(f"  {r['h']:4d} {cf_ok:7.2f} {cf_dis:6.2f} {dLW:10.1f}")
    if n:
        print(f"  → средно за нощта: {tot/n:.1f} W/m² по-малко LW надолу")

    # ── Обобщение ──
    print()
    print("  ОБОБЩЕНИЕ")
    bases = [r["base_m"] for r in rows_for_stats if r["base_m"] is not None]
    viss  = [r["vis"]    for r in rows_for_stats if r["vis"]    is not None]
    rh2s  = [r["rh2"]    for r in rows_for_stats if r["rh2"]    is not None]
    los   = [r["lo"]     for r in rows_for_stats]

    if bases:
        print(f"    METAR облачна база (BKN/OVC): мин={min(bases):.0f} m  "
              f"макс={max(bases):.0f} m  часове с база={len(bases)}/{len(rows_for_stats)}")
    else:
        print("    METAR облачна база: НЯМА BKN/OVC през нощта")
    if viss:
        print(f"    METAR видимост: мин={min(viss)} m  макс={max(viss)} m")
    if los:
        print(f"    ICON ниска облачност: мин={min(los):.2f}  макс={max(los):.2f}")
    if rh2s:
        print(f"    ICON RH2m: мин={min(rh2s):.2f}  макс={max(rh2s):.2f}")

    # Диагноза
    print()
    print("  ДИАГНОЗА")
    hi_lo = [r for r in rows_for_stats if r["lo"] > 0.5]
    if not hi_lo:
        print("    ICON не дава значима ниска облачност — дисконтът е без ефект.")
    else:
        # Има ли реален облак горе, при ненаситена ICON земя?
        rh_low  = [r for r in hi_lo if r["rh2"] is not None and r["rh2"] < 0.95]
        base_hi = [r for r in hi_lo if r["base_m"] is not None and r["base_m"] > 200]
        if rh_low and base_hi:
            print("    ICON дава плътна ниска облачност при НЕнаситена земя (RH2m<0.95),")
            print("    а METAR потвърждава база над 200 m. Тоест облакът е ГОРЕ и е")
            print("    легитимен източник на LW надолу — дисконтът го изтрива погрешно.")
        elif not rh_low:
            print("    ICON RH2m е наситен заедно с ниската облачност — ICON вижда")
            print("    мъгла/много нисък стратус на самата земя. Дисконтът е")
            print("    концептуално оправдан; преохлаждането има друг източник.")
        else:
            print("    Смесена картина — вижте таблицата ред по ред.")

    print()


def main():
    ap = argparse.ArgumentParser(
        description="Диагностика на облачния дисконт при RH>95%")
    ap.add_argument("cases", nargs="*", help="case_id, напр. LBSF_CLDY_2024-12-10")
    ap.add_argument("--cases-dir", default="cases")
    ap.add_argument("--cache",     default="icon_cache")
    ap.add_argument("--hour",      type=int, default=18)
    ap.add_argument("--fh",        type=int, default=16)
    ap.add_argument("--all-cldy-fa", action="store_true",
                    help="Всички CLDY случаи с изход FA от diagnostic_summary.json")
    ap.add_argument("--diag", default="diagnostic_summary.json")
    args = ap.parse_args()

    targets = list(args.cases)

    if args.all_cldy_fa:
        if not os.path.exists(args.diag):
            print(f"[ГРЕШКА] Няма {args.diag}")
            return
        with open(args.diag, encoding="utf-8") as f:
            diag = json.load(f)
        extra = [r["case"] for r in diag
                 if r.get("category") == "CLDY" and r.get("event") == "FA"]
        for c in extra:
            if c not in targets:
                targets.append(c)
        print(f"[INFO] CLDY + FA случаи от {args.diag}: {len(extra)}\n")

    if not targets:
        ap.print_help()
        return

    for c in targets:
        probe(c, args.cases_dir, args.cache, args.hour, args.fh)


if __name__ == "__main__":
    main()
