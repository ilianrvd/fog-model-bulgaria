"""
build_diagnostic.py
===================
Генерира diagnostic_summary.json от:
  1. logs/verify_*.json      — regime_log, event, Tmin_err, mod_min_vis
  2. cases/*.txt             — METAR-и за нощта (вятър, посока, порив)
  3. icon_cache/*.json       — ICON почасов вятър на най-ниското ниво

Употреба:
  python build_diagnostic.py --verify logs/verify_2026-07-31_1938.json
                             --cases  cases/
                             --cache  icon_cache/
                             --out    diagnostic_summary.json

Резултатът е списък от записи — по един на случай.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta


# ──────────────────────────────────────────────────────────────
# METAR парсинг (само вятър и час — без metar_parser зависимост)
# ──────────────────────────────────────────────────────────────

def parse_metar_wind(raw: str) -> dict:
    """
    Извлича от суров METAR ред:
      hour_utc, minute, wind_dir (° или None при VRB), wind_kt, gust_kt
    """
    result = {"hour_utc": None, "minute": None,
              "wind_dir": None, "wind_kt": None, "gust_kt": None}

    # Timestamp: DDHHMMZ
    m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', raw)
    if m:
        result["hour_utc"] = int(m.group(2))
        result["minute"]   = int(m.group(3))

    # Вятър: DDDssGggKT или VRBssKT
    m = re.search(r'\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b', raw)
    if m:
        d = m.group(1)
        result["wind_dir"] = None if d == "VRB" else int(d)
        result["wind_kt"]  = int(m.group(2))
        result["gust_kt"]  = int(m.group(3)) if m.group(3) else None

    return result


def in_sw_sector(wind_dir, lo=190, hi=250) -> bool:
    """Дали посоката е в сектора [lo, hi]°."""
    if wind_dir is None:
        return False
    return lo <= wind_dir <= hi


# ──────────────────────────────────────────────────────────────
# Парсинг на case файл
# ──────────────────────────────────────────────────────────────

def parse_case_file(path: str) -> list:
    """
    Чете case/*.txt и връща списък от METAR wind записи.
    Само редовете, започващи с 'METAR ' се обработват.
    """
    winds = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("METAR "):
                    continue
                w = parse_metar_wind(line)
                if w["hour_utc"] is not None and w["wind_kt"] is not None:
                    winds.append(w)
    except FileNotFoundError:
        pass
    return winds


# ──────────────────────────────────────────────────────────────
# icon_cache четене
# ──────────────────────────────────────────────────────────────

def load_icon_cache(cache_dir: str, icao: str, date_str: str,
                    hour0: int = 18) -> list:
    """
    Зарежда icon_cache файл и връща почасов вятър на най-ниското ниво.
    Именуване: LBSF_2024-01-15_18_16.json
    Връща list of dict: {hour_utc, wind_kt, wind_dir, z_m}
    """
    fname = f"{icao}_{date_str}_{hour0}_16.json"
    path  = os.path.join(cache_dir, fname)
    if not os.path.exists(path):
        return []

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    result = []
    icon_z = None  # височина на приземното ниво (за справка)

    for prof in data.get("hourly_profiles", []):
        time_str = prof.get("time", "")
        # Извличаме час от 'YYYY-MM-DDTHH:MM'
        m = re.search(r'T(\d{2}):', time_str)
        if not m:
            continue
        h = int(m.group(1))

        u_arr = prof.get("u", [])
        v_arr = prof.get("v", [])
        z_arr = prof.get("z", [])
        if not u_arr or not v_arr:
            continue

        u0 = float(u_arr[0])
        v0 = float(v_arr[0])
        spd_kt  = math.sqrt(u0**2 + v0**2) * 1.94384
        wind_dir = (math.degrees(math.atan2(-u0, -v0)) + 360) % 360
        z0 = float(z_arr[0]) if z_arr else None

        if icon_z is None:
            icon_z = z0

        result.append({
            "hour_utc":  h,
            "wind_kt":   round(spd_kt, 1),
            "wind_dir":  round(wind_dir, 0),
            "z_m":       round(z0, 0) if z0 else None,
        })

    return result


# ──────────────────────────────────────────────────────────────
# Нощен прозорец: 18–07 UTC (може да прескача полунощ)
# ──────────────────────────────────────────────────────────────

def in_night_window(hour: int) -> bool:
    """True ако часът е в нощния прозорец 18–07 UTC."""
    return hour >= 18 or hour <= 7


# ──────────────────────────────────────────────────────────────
# Построяване на един диагностичен запис
# ──────────────────────────────────────────────────────────────

def build_record(res: dict, cases_dir: str, cache_dir: str) -> dict:
    case_id  = res["case_id"]
    icao     = res["icao"]
    category = res["category"]
    date     = res["date"]
    ev       = res["eval"]
    rl       = res.get("regime_log", [])

    # ── Основни полета ──
    record = {
        "case":     case_id,
        "icao":     icao,
        "category": category,
        "date":     date,
        "event":    ev.get("event"),
        # Изключени като валежни (verify_cases.py, 11.08.2026). Всеки
        # анализ трябва да ги филтрира — иначе брои случаи, чиято ниска
        # видимост не е от мъгла.
        "excluded":        res.get("excluded", False),
        "excluded_reason": res.get("excluded_reason"),
        "obs_vis_cause":   res.get("obs_vis_cause", {}),
    }

    # ── Режимен лог ──
    regime_start        = rl[0]["regime"]  if rl else None
    regime_start_reason = rl[0]["reason"]  if rl else None

    # Смени на режима (без изгрева — "Изгрев → nudging T" не е синоптична смяна)
    changes = []
    for i in range(1, len(rl)):
        prev = rl[i-1]
        curr = rl[i]
        if curr["regime"] != prev["regime"]:
            is_sunrise = "зрев" in curr.get("reason", "") or \
                         "nudging" in curr.get("reason", "").lower()
            changes.append({
                "hour_utc": curr["hour_utc"],
                "from":     prev["regime"],
                "to":       curr["regime"],
                "reason":   curr.get("reason", ""),
                "sunrise":  is_sunrise,
            })

    # Стабилен = без смени преди изгрева
    non_sunrise_changes = [c for c in changes if not c["sunrise"]]
    regime_was_stable   = len(non_sunrise_changes) == 0

    record["regime_start"]        = regime_start
    record["regime_start_reason"] = regime_start_reason
    record["regime_changes"]      = changes
    record["regime_was_stable"]   = regime_was_stable

    # ── Температура ──
    T_info = ev.get("T", {})
    record["T_MAE"]      = round(T_info.get("MAE", None) or 0, 3) \
                           if T_info.get("MAE") is not None else None
    record["Tmin_err"]   = round(T_info.get("Tmin_err", None) or 0, 3) \
                           if T_info.get("Tmin_err") is not None else None
    record["T_err_0306"] = round(T_info.get("err_0306", None) or 0, 3) \
                           if T_info.get("err_0306") is not None else None

    # ── Видимост ──
    record["vis_min_mod"]  = ev.get("mod_min_vis")
    record["fog_hours_mod"] = ev.get("hourly", {}).get("fa", 0) + \
                              ev.get("hourly", {}).get("hits", 0)
    record["fog_hours_obs"] = ev.get("hourly", {}).get("misses", 0) + \
                              ev.get("hourly", {}).get("hits", 0)

    # ── METAR вятър от case файл ──
    case_path = os.path.join(cases_dir, f"{case_id}.txt")
    metar_winds = parse_case_file(case_path)

    # Само нощния прозорец
    night_winds = [w for w in metar_winds if in_night_window(w["hour_utc"])]

    record["wind_metar"] = [
        {
            "hour_utc": w["hour_utc"],
            "minute":   w["minute"],
            "wind_kt":  w["wind_kt"],
            "wind_dir": w["wind_dir"],
            "gust_kt":  w["gust_kt"],
        }
        for w in night_winds
    ]

    # Обобщени METAR статистики
    spds = [w["wind_kt"] for w in night_winds if w["wind_kt"] is not None]
    record["wind_kt_mean_night"] = round(sum(spds)/len(spds), 1) if spds else None
    record["wind_kt_max_night"]  = max(spds) if spds else None
    record["wind_kt_start"]      = spds[0]   if spds else None

    # Посока при старта (18 UTC)
    start_winds = [w for w in night_winds
                   if w["hour_utc"] == 18 and w["minute"] == 0]
    record["wind_dir_start"] = start_winds[0]["wind_dir"] \
                               if start_winds else \
                               (night_winds[0]["wind_dir"] if night_winds else None)

    # ── SW сектор (190–250°) ──
    sw_hours = sum(1 for w in night_winds if in_sw_sector(w["wind_dir"]))
    record["sw_sector_hours"] = sw_hours

    # ── ICON вятър от icon_cache ──
    icon_winds = load_icon_cache(cache_dir, icao, date)
    night_icon = [w for w in icon_winds if in_night_window(w["hour_utc"])]

    record["wind_icon"] = night_icon
    record["wind_icon_z_m"] = night_icon[0]["z_m"] if night_icon else None

    # Обобщени ICON статистики
    ispds = [w["wind_kt"] for w in night_icon]
    record["icon_kt_mean_night"] = round(sum(ispds)/len(ispds), 1) if ispds else None
    record["icon_kt_max_night"]  = max(ispds) if ispds else None
    record["icon_kt_start"]      = ispds[0]   if ispds else None

    return record


# ──────────────────────────────────────────────────────────────
# Главна функция
# ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Генерира diagnostic_summary.json")
    ap.add_argument("--verify", required=True,
                    help="Път до verify JSON (logs/verify_*.json)")
    ap.add_argument("--cases",  required=True,
                    help="Директория с case файлове (cases/)")
    ap.add_argument("--cache",  required=True,
                    help="Директория с icon_cache файлове (icon_cache/)")
    ap.add_argument("--out",    default="diagnostic_summary.json",
                    help="Изходен файл (по подразбиране: diagnostic_summary.json)")
    args = ap.parse_args()

    # Зареждаме verify JSON
    with open(args.verify, encoding="utf-8") as f:
        verify_data = json.load(f)
    results = verify_data["results"]
    print(f"[INFO] Заредени {len(results)} случая от {args.verify}")

    # Строим диагностичните записи
    records = []
    missing_cache  = 0
    missing_case   = 0

    for i, res in enumerate(results):
        rec = build_record(res, args.cases, args.cache)
        records.append(rec)

        if not rec["wind_metar"]:
            missing_case += 1
        if not rec["wind_icon"]:
            missing_cache += 1

        if (i+1) % 50 == 0:
            print(f"  ... {i+1}/{len(results)}")

    print(f"[INFO] Готово. Без case файл: {missing_case}. Без icon_cache: {missing_cache}.")

    # Записваме
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"[OK]  Записан {args.out}  ({len(records)} записа)")

    # Кратка статистика
    print_summary(records)


def print_summary(records):
    """Кратка статистика на конзолата след генерирането."""
    from collections import Counter

    print()
    print("=" * 60)
    print("КРАТКА СТАТИСТИКА")
    print("=" * 60)

    n_excl = sum(1 for r in records if r.get("excluded"))
    scored = [r for r in records if not r.get("excluded")]
    if n_excl:
        print(f"\n  {n_excl} изключени (валеж) — не участват в статистиката")

    # По летище и изход
    by_icao = {}
    for r in scored:
        icao = r["icao"]
        ev   = r["event"]
        if icao not in by_icao:
            by_icao[icao] = Counter()
        by_icao[icao][ev] += 1

    print(f"\n{'ICAO':<6} {'HIT':>4} {'MISS':>5} {'FA':>4} {'CN':>4} {'CSI':>7}")
    print("-" * 34)
    _th = _tm = _tf = 0
    for icao in sorted(by_icao):
        c = by_icao[icao]
        h, m, f = c['HIT'], c['MISS'], c['FA']
        _th += h; _tm += m; _tf += f
        csi = h / (h + m + f) if (h + m + f) else float('nan')
        print(f"{icao:<6} {h:>4} {m:>5} {f:>4} {c['CN']:>4} {csi:>7.3f}")
    _csi = _th / (_th + _tm + _tf) if (_th + _tm + _tf) else float("nan")
    print("-" * 34)
    print(f"{'ОБЩО':<6} {_th:>4} {_tm:>5} {_tf:>4} "
          f"{sum(by_icao[i]['CN'] for i in by_icao):>4} {_csi:>7.3f}")

    # Режимни смени при CFOG случаи
    print("\nРежимни смени при CFOG (non-sunrise):")
    cfog = [r for r in scored if r["category"] == "CFOG"]
    stable   = [r for r in cfog if r["regime_was_stable"]]
    unstable = [r for r in cfog if not r["regime_was_stable"]]
    print(f"  Стабилен режим: {len(stable)}  Нестабилен: {len(unstable)}")

    # Среден вятър при старт по изход (CFOG)
    print("\nСреден METAR вятър при старт (18 UTC) по изход (CFOG):")
    for ev in ("HIT", "MISS", "FA", "CN"):
        grp = [r["wind_kt_start"] for r in cfog
               if r["event"] == ev and r["wind_kt_start"] is not None]
        if grp:
            print(f"  {ev}: n={len(grp):2d}  mean={sum(grp)/len(grp):.1f}kt"
                  f"  max={max(grp):.0f}kt")

    # SW сектор при LBBG
    lbbg = [r for r in scored if r["icao"] == "LBBG"]
    sw   = [r for r in lbbg if r["sw_sector_hours"] > 0]
    print(f"\nLBBG с ≥1 час в 190–250° сектор: {len(sw)} / {len(lbbg)}")
    if sw:
        ev_sw = Counter(r["event"] for r in sw)
        print(f"  По изход: {dict(ev_sw)}")

    print("=" * 60)


if __name__ == "__main__":
    main()
