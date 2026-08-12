"""
probe_init_profile.py
=====================
Измерва какво прави `build_surface_layer` при инициализация за всеки
случай, БЕЗ да пуска модела.

Възпроизвежда точно логиката от run_case.build_surface_layer:
    dT_dz = clip((T_icon[0] - T_metar) / z_icon[0], -10/1000, +30/1000)
    T(z)  = T_metar + dT_dz * z        за z = 2 .. 292 m (стъпка 10)
после залепва ICON от първото ниво над 300 m.

Измерва:
  - dT_dz            [K/km]  и дали е ударил тавана (+30) / пода (-10)
  - dT_total_300     [K]     общата инверсия, натрупана до 292 m
  - jump_grad        [K/km]  градиентът в СКОКА между 292 m и ICON горе
  - RH_init на 2 m и на 292 m
и ги свързва с изхода (event) и Tmin_err от diagnostic_summary.json.

Употреба:
  python probe_init_profile.py
  python probe_init_profile.py --csv init_profile.csv
  python probe_init_profile.py --icao LBSF --category CLDY

Нула пускания на модела.
"""

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# Константи, съгласувани с fog_model
EPS_R = 0.622
DTDZ_MAX = +30.0 / 1000.0    # таван при clip
DTDZ_MIN = -10.0 / 1000.0    # под при clip


def sat_vapor_pressure(T_K):
    """Насищащо парно налягане [Pa]. Magnus, съгласувано с модела."""
    Tc = T_K - 273.15
    return 611.2 * math.exp(17.62 * Tc / (243.12 + Tc))


def qsat(T_K, p_Pa):
    es = sat_vapor_pressure(T_K)
    return EPS_R * es / max(p_Pa - es, 1.0)


# ──────────────────────────────────────────────────────────────
# METAR: T/Td/час от стартовото наблюдение
# ──────────────────────────────────────────────────────────────

def start_metar(case_path, hour0=18):
    """Връща (T_C, Td_C) от METAR най-близо до hour0:00, или (None, None)."""
    best = None
    best_diff = 10 ** 9
    try:
        with open(case_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("METAR "):
                    continue
                m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', line)
                if not m:
                    continue
                h, mn = int(m.group(2)), int(m.group(3))
                diff = abs(h * 60 + mn - hour0 * 60)
                if diff > 12 * 60:
                    diff = 24 * 60 - diff
                mt = re.search(r'\b(M?\d{2})/(M?\d{2})\b', line)
                if not mt:
                    continue
                if diff < best_diff:
                    def _t(s):
                        return -int(s[1:]) if s.startswith("M") else int(s)
                    best = (_t(mt.group(1)), _t(mt.group(2)))
                    best_diff = diff
    except FileNotFoundError:
        return None, None
    return best if best else (None, None)


# ──────────────────────────────────────────────────────────────
# Ядро: възпроизвеждане на build_surface_layer
# ──────────────────────────────────────────────────────────────

def analyse(icon, T_met_C, Td_met_C, hour0=18):
    """
    Връща dict с измерванията, или None ако данните не стигат.
    """
    z = icon.get("z", [])
    T = icon.get("T", [])
    p = icon.get("p", [])
    if not z or not T or T_met_C is None:
        return None

    T_met = T_met_C + 273.15
    Td_met = (Td_met_C + 273.15) if Td_met_C is not None else T_met - 3.0
    z0, T0 = float(z[0]), float(T[0])
    p_sfc = float(p[0]) if p else 95000.0

    # ── dT_dz, точно както в build_surface_layer ──
    at_cap = at_floor = False
    if z0 > 10.0:
        raw = (T0 - T_met) / z0
        dTdz = raw
        if dTdz > DTDZ_MAX:
            dTdz, at_cap = DTDZ_MAX, True
        elif dTdz < DTDZ_MIN:
            dTdz, at_floor = DTDZ_MIN, True
        source = "METAR→ICON"
    else:
        is_night = (hour0 >= 18 or hour0 <= 8)
        dTdz = (+3.0 / 1000.0) if is_night else (-6.5 / 1000.0)
        raw = dTdz
        source = "fallback"

    # ── профилът, който се строи: 2..292 m ──
    z_top = 292.0
    T_top = T_met + dTdz * z_top
    dT_total = T_top - T_met            # натрупана инверсия до 292 m

    # ── залепване: първото ICON ниво над 300 m ──
    idx = None
    for i, zz in enumerate(z):
        if float(zz) > 300.0:
            idx = i
            break
    if idx is None:
        idx = len(z) - 1
    z_up, T_up = float(z[idx]), float(T[idx])
    jump_grad = (T_up - T_top) / max(z_up - z_top, 1.0) * 1000.0   # K/km

    # ── RH в инициализирания профил ──
    es_sfc = sat_vapor_pressure(Td_met)
    qv_sfc = EPS_R * es_sfc / (p_sfc - es_sfc)

    T_2 = T_met + dTdz * 2.0
    p_2 = p_sfc * math.exp(-9.81 * 2.0 / (287.0 * T_met))
    rh_2 = qv_sfc * math.exp(-2.0 / 800.0) / qsat(T_2, p_2)

    qv_top = qv_sfc * math.exp(-z_top / 800.0)
    p_top = p_sfc * math.exp(-9.81 * z_top / (287.0 * T_met))
    rh_top = qv_top / qsat(T_top, p_top)

    return {
        "z_icon0":    z0,
        "dTdz_raw":   raw * 1000.0,
        "dTdz":       dTdz * 1000.0,
        "at_cap":     at_cap,
        "at_floor":   at_floor,
        "source":     source,
        "dT_total":   dT_total,
        "T_292":      T_top - 273.15,
        "z_join":     z_up,
        "T_join":     T_up - 273.15,
        "jump_grad":  jump_grad,
        "rh_2":       rh_2,
        "rh_292":     rh_top,
    }


# ──────────────────────────────────────────────────────────────
# Статистика
# ──────────────────────────────────────────────────────────────

def corr(xs, ys):
    """Пирсънова корелация; None при недостатъчни данни."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def bucket_report(rows, key, edges, label):
    """Разбива по кофи и печата Tmin_err по кофа."""
    print(f"\n  {label}")
    print(f"  {'кофа':>18}{'n':>5}{'Tmin_err ср.':>14}{'медиана':>10}"
          f"{'FA':>5}{'HIT':>5}{'MISS':>6}{'CN':>5}")
    print("  " + "-" * 68)
    for lo, hi in edges:
        grp = [r for r in rows
               if r["m"][key] is not None
               and lo <= r["m"][key] < hi
               and r["Tmin_err"] is not None]
        if not grp:
            continue
        errs = [r["Tmin_err"] for r in grp]
        ev = {}
        for r in grp:
            ev[r["event"]] = ev.get(r["event"], 0) + 1
        lo_s = "-inf" if lo == float("-inf") else f"{lo:.0f}"
        hi_s = "+inf" if hi == float("inf") else f"{hi:.0f}"
        print(f"  {f'[{lo_s}, {hi_s})':>18}{len(grp):>5}"
              f"{statistics.mean(errs):>+14.2f}{statistics.median(errs):>+10.2f}"
              f"{ev.get('FA',0):>5}{ev.get('HIT',0):>5}"
              f"{ev.get('MISS',0):>6}{ev.get('CN',0):>5}")


def main():
    ap = argparse.ArgumentParser(
        description="Измерва инициализационния профил за всички случаи")
    ap.add_argument("--diag",  default="diagnostic_summary.json")
    ap.add_argument("--cases", default="cases")
    ap.add_argument("--cache", default="icon_cache")
    ap.add_argument("--hour",  type=int, default=18)
    ap.add_argument("--fh",    type=int, default=16)
    ap.add_argument("--csv",   default=None, help="Записва подробностите в CSV")
    ap.add_argument("--icao",     default=None)
    ap.add_argument("--category", default=None)
    args = ap.parse_args()

    with open(args.diag, encoding="utf-8") as f:
        diag = json.load(f)

    rows = []
    skipped = 0

    for rec in diag:
        if args.icao and rec["icao"] != args.icao:
            continue
        if args.category and rec.get("category") != args.category:
            continue

        case = rec["case"]
        icao = rec["icao"]
        date = rec["date"]

        ipath = os.path.join(args.cache,
                             f"{icao}_{date}_{args.hour:02d}_{args.fh}.json")
        cpath = os.path.join(args.cases, case + ".txt")
        if not os.path.exists(ipath):
            skipped += 1
            continue

        try:
            with open(ipath, encoding="utf-8") as f:
                icon = json.load(f)
        except Exception:
            skipped += 1
            continue

        T_C, Td_C = start_metar(cpath, args.hour)
        m = analyse(icon, T_C, Td_C, args.hour)
        if m is None:
            skipped += 1
            continue

        rows.append({
            "case": case, "icao": icao,
            "category": rec.get("category"),
            "event": rec.get("event"),
            "Tmin_err": rec.get("Tmin_err"),
            "T_MAE": rec.get("T_MAE"),
            "m": m,
        })

    print(f"[INFO] Анализирани {len(rows)} случая, пропуснати {skipped}")

    if not rows:
        print("[ГРЕШКА] Няма данни.")
        return

    # ── 1. Разпределение на dT_dz ──
    dtdz = [r["m"]["dTdz"] for r in rows]
    caps = sum(1 for r in rows if r["m"]["at_cap"])
    floors = sum(1 for r in rows if r["m"]["at_floor"])
    fbs = sum(1 for r in rows if r["m"]["source"] == "fallback")

    print("\n" + "=" * 72)
    print("1. ИНИЦИАЛИЗАЦИОНЕН ГРАДИЕНТ dT_dz [K/km]")
    print("=" * 72)
    print(f"  средно={statistics.mean(dtdz):+.1f}   "
          f"медиана={statistics.median(dtdz):+.1f}   "
          f"мин={min(dtdz):+.1f}   макс={max(dtdz):+.1f}")
    print(f"  ударили тавана +30 K/km : {caps}  ({caps/len(rows)*100:.0f}%)")
    print(f"  ударили пода   -10 K/km : {floors}")
    print(f"  fallback (z_icon0<10m)  : {fbs}")

    # ── 2. Натрупана инверсия до 292 m ──
    dtot = [r["m"]["dT_total"] for r in rows]
    print("\n" + "=" * 72)
    print("2. НАТРУПАНА ИНВЕРСИЯ ДО 292 m [K]  (= dT_dz * 0.292)")
    print("=" * 72)
    print(f"  средно={statistics.mean(dtot):+.2f}   "
          f"медиана={statistics.median(dtot):+.2f}   макс={max(dtot):+.2f}")
    big = [r for r in rows if r["m"]["dT_total"] > 3.0]
    print(f"  случаи с инверсия > 3 K до 292 m: {len(big)}  "
          f"({len(big)/len(rows)*100:.0f}%)")

    # ── 3. Скокът при залепването ──
    jumps = [r["m"]["jump_grad"] for r in rows]
    steep = [r for r in rows if r["m"]["jump_grad"] < -9.8]
    print("\n" + "=" * 72)
    print("3. ГРАДИЕНТ В СКОКА 292 m → ICON [K/km]")
    print("=" * 72)
    print(f"  средно={statistics.mean(jumps):+.1f}   "
          f"медиана={statistics.median(jumps):+.1f}   мин={min(jumps):+.1f}")
    print(f"  по-стръмни от сух адиабат (-9.8 K/km): {len(steep)}  "
          f"({len(steep)/len(rows)*100:.0f}%)")

    # ── 4. Корелации с Tmin_err ──
    print("\n" + "=" * 72)
    print("4. КОРЕЛАЦИЯ С Tmin_err")
    print("=" * 72)
    valid = [r for r in rows if r["Tmin_err"] is not None]
    for key, lbl in (("dTdz", "dT_dz"),
                     ("dT_total", "инверсия до 292 m"),
                     ("jump_grad", "скок при залепването"),
                     ("rh_2", "RH_init на 2 m")):
        xs = [r["m"][key] for r in valid]
        ys = [r["Tmin_err"] for r in valid]
        c = corr(xs, ys)
        c_s = f"{c:+.3f}" if c is not None else "n/a"
        print(f"  {lbl:<26} r = {c_s}   (n={len(xs)})")

    # ── 5. Кофи ──
    print("\n" + "=" * 72)
    print("5. Tmin_err ПО КОФИ")
    print("=" * 72)
    bucket_report(valid, "dTdz",
                  [(float("-inf"), 0), (0, 5), (5, 10), (10, 20),
                   (20, 29.99), (29.99, float("inf"))],
                  "по dT_dz [K/km]  (последната кофа = ударили тавана)")
    bucket_report(valid, "jump_grad",
                  [(float("-inf"), -30), (-30, -20), (-20, -9.8),
                   (-9.8, 0), (0, float("inf"))],
                  "по градиента в скока [K/km]")

    # ── 6. По летище ──
    print("\n" + "=" * 72)
    print("6. ПО ЛЕТИЩЕ")
    print("=" * 72)
    print(f"  {'ICAO':<7}{'n':>4}{'dT_dz ср.':>11}{'таван':>7}"
          f"{'скок ср.':>11}{'Tmin_err':>10}")
    print("  " + "-" * 52)
    for icao in sorted(set(r["icao"] for r in rows)):
        grp = [r for r in rows if r["icao"] == icao]
        d = [r["m"]["dTdz"] for r in grp]
        j = [r["m"]["jump_grad"] for r in grp]
        e = [r["Tmin_err"] for r in grp if r["Tmin_err"] is not None]
        nc = sum(1 for r in grp if r["m"]["at_cap"])
        e_s = f"{statistics.mean(e):+.2f}" if e else "n/a"
        print(f"  {icao:<7}{len(grp):>4}{statistics.mean(d):>+11.1f}"
              f"{nc:>7}{statistics.mean(j):>+11.1f}{e_s:>10}")

    # ── 7. Топ 15 по стръмнина на скока ──
    print("\n" + "=" * 72)
    print("7. НАЙ-СТРЪМНИ СКОКОВЕ (топ 15)")
    print("=" * 72)
    print(f"  {'случай':<26}{'dT_dz':>9}{'инв.':>7}{'скок':>8}"
          f"{'изход':>7}{'Tmin_err':>10}")
    print("  " + "-" * 67)
    for r in sorted(rows, key=lambda x: x["m"]["jump_grad"])[:15]:
        t = f"{r['Tmin_err']:+.2f}" if r["Tmin_err"] is not None else "n/a"
        print(f"  {r['case']:<26}{r['m']['dTdz']:>+9.1f}"
              f"{r['m']['dT_total']:>+7.1f}{r['m']['jump_grad']:>+8.0f}"
              f"{r['event'] or '--':>7}{t:>10}")

    # ── CSV ──
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case", "icao", "category", "event", "Tmin_err",
                        "T_MAE", "z_icon0", "dTdz_raw", "dTdz", "at_cap",
                        "source", "dT_total", "T_292", "z_join", "T_join",
                        "jump_grad", "rh_2", "rh_292"])
            for r in rows:
                m = r["m"]
                w.writerow([r["case"], r["icao"], r["category"], r["event"],
                            r["Tmin_err"], r["T_MAE"],
                            f"{m['z_icon0']:.0f}", f"{m['dTdz_raw']:.2f}",
                            f"{m['dTdz']:.2f}", int(m["at_cap"]), m["source"],
                            f"{m['dT_total']:.2f}", f"{m['T_292']:.2f}",
                            f"{m['z_join']:.0f}", f"{m['T_join']:.2f}",
                            f"{m['jump_grad']:.1f}",
                            f"{m['rh_2']:.3f}", f"{m['rh_292']:.3f}"])
        print(f"\n[OK] Записан {args.csv}")


if __name__ == "__main__":
    main()
