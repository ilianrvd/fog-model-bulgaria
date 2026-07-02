"""
ogimet_fetcher.py
=================
Изтегля исторически METAR от OGIMET за българските летища.
Работи с глобален архив от десетилетия назад.

URL формат:
  https://www.ogimet.com/cgi-bin/getmetar?icao=LBSF&begin=202401151800&end=202401160800

CSV формат на отговора:
  LBSF,2024,01,15,18,00,METAR LBSF 151800Z VRB03KT 5000 BR ...=

Употреба
--------
from ogimet_fetcher import fetch_all_ogimet, find_obs_at, obs_to_metar_dict

all_obs = fetch_all_ogimet(["LBSF","LBGO"], "2024-01-15", hour0=18, hours=14)
obs, h, mn = find_obs_at(all_obs, "LBSF", target_hour=18, date_str="2024-01-15")
metar_dict = obs_to_metar_dict(obs)
"""

import urllib.request
import urllib.parse
import time
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from metar_parser import parse_metar


# ──────────────────────────────────────────────────────────────
# WMO/ICAO таблица за България
# ──────────────────────────────────────────────────────────────

BG_AIRPORTS = {
    "LBSF": {"wmo": "15614", "name": "София"},
    "LBWN": {"wmo": "15552", "name": "Варна"},
    "LBBG": {"wmo": "15612", "name": "Бургас"},
    "LBPD": {"wmo": "15634", "name": "Пловдив"},
    "LBGO": {"wmo": "15541", "name": "Г. Оряховица"},
}


# ──────────────────────────────────────────────────────────────
# Fetcher
# ──────────────────────────────────────────────────────────────

def fetch_metar_ogimet(icao: str, date_str: str,
                       hour0: int = 0, hours: int = 26,
                       sleep_s: float = 1.0) -> list:
    """
    Изтегля METAR от OGIMET за летище и период.

    Параметри
    ----------
    icao     : ICAO код
    date_str : начална дата 'YYYY-MM-DD'
    hour0    : начален UTC час
    hours    : брой часове напред (default 26 — покрива прогноза + буфер)
    sleep_s  : пауза след заявката (учтивост към OGIMET)

    Връща list of dict — съвместим с iem_fetcher формата
    """
    from datetime import datetime, timedelta

    dt_start = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour0)
    dt_end   = dt_start + timedelta(hours=hours)

    begin = dt_start.strftime("%Y%m%d%H%M")
    end   = dt_end.strftime("%Y%m%d%H%M")

    url = (f"https://www.ogimet.com/cgi-bin/getmetar?"
           f"icao={icao}&begin={begin}&end={end}&lang=en")

    print(f"[OGIMET] {icao} {date_str} {hour0:02d}UTC → +{hours}h")
    print(f"[OGIMET] URL: {url}")

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; fog-model-dprvd/1.0)",
        "Accept"    : "text/plain,text/html,*/*",
    })

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OGIMET HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"OGIMET мрежова грешка: {e.reason}")

    time.sleep(sleep_s)

    # Парсираме CSV редовете
    result = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Формат: LBSF,2024,01,15,18,00,METAR LBSF 151800Z ...=
        parts = line.split(",", 6)
        if len(parts) < 7:
            continue

        try:
            station = parts[0].strip()
            year    = int(parts[1])
            month   = int(parts[2])
            day     = int(parts[3])
            hour    = int(parts[4])
            minute  = int(parts[5])
            raw     = parts[6].strip().rstrip("=").strip()
        except (ValueError, IndexError):
            continue

        if station != icao:
            continue

        t_str = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00Z"

        # Парсираме METAR
        m = parse_metar(raw)

        result.append({
            "time"     : t_str,
            "raw"      : raw,
            "T"        : m.get("T"),
            "Td"       : m.get("Td"),
            "vis_m"    : m.get("visibility"),
            "wind_dir" : m.get("wind_dir"),
            "wind_kt"  : m.get("wind_speed"),
            "weather"  : m.get("weather", []),
            "ceiling"  : m.get("ceiling"),
            "vv"       : m.get("vv"),
            "QNH"      : m.get("QNH"),
        })

    result.sort(key=lambda x: x["time"])
    print(f"[OGIMET] {icao}: {len(result)} наблюдения")
    if result:
        r0, r1 = result[0], result[-1]
        print(f"         {r0['time']}  T={r0['T']}°C  VIS={r0['vis_m']}m")
        print(f"         {r1['time']}  T={r1['T']}°C  VIS={r1['vis_m']}m")

    return result


def fetch_all_ogimet(icao_list: list, date_str: str,
                     hour0: int = 0, hours: int = 26) -> dict:
    """Изтегля METAR от OGIMET за всички летища."""
    result = {}
    for icao in icao_list:
        try:
            result[icao] = fetch_metar_ogimet(icao, date_str, hour0, hours)
        except Exception as e:
            print(f"[OGIMET] ⚠ {icao}: {e}")
            result[icao] = []
    return result


# ──────────────────────────────────────────────────────────────
# Намиране на наблюдение за даден час
# ──────────────────────────────────────────────────────────────

def find_obs_at(all_obs: dict, icao: str,
                target_hour: int, date_str: str,
                window_min: int = 90) -> tuple:
    """
    Намира наблюдение най-близо до target_hour UTC.
    Връща: (obs_dict | None, actual_hour, actual_minute)
    """
    obs_list = all_obs.get(icao, [])
    target_min = target_hour * 60
    best      = None
    best_diff = window_min + 1
    best_h, best_mn = target_hour, 0

    for obs in obs_list:
        t = obs["time"]
        m = re.search(r'T(\d{2}):(\d{2})', t)
        if not m:
            continue
        if date_str not in t:
            continue
        h, mn = int(m[1]), int(m[2])
        diff = abs(h * 60 + mn - target_min)
        if diff < best_diff:
            best_diff = diff
            best      = obs
            best_h, best_mn = h, mn

    if best:
        print(f"[OGIMET] {icao}: намерено @ {best_h:02d}:{best_mn:02d} UTC "
              f"(±{best_diff}min от {target_hour:02d}:00)")
    return best, best_h, best_mn


def obs_to_metar_dict(obs: dict) -> dict:
    """Конвертира OGIMET obs към формата на metar_parser."""
    if obs is None:
        return {}
    return {
        "T"          : obs.get("T"),
        "Td"         : obs.get("Td"),
        "visibility" : obs.get("vis_m"),
        "wind_dir"   : obs.get("wind_dir"),
        "wind_speed" : obs.get("wind_kt"),
        "weather"    : obs.get("weather", []),
        "raw"        : obs.get("raw", ""),
        "hour"       : int(obs["time"][11:13]) if len(obs.get("time","")) >= 13 else 0,
    }


# ──────────────────────────────────────────────────────────────
# Верификация (идентична с iem_fetcher)
# ──────────────────────────────────────────────────────────────

def verify_forecast(history: list, icao: str,
                    date_str: str, hour0: int,
                    all_obs: dict) -> dict:
    """
    Верифицира прогнозата hour-by-hour срещу OGIMET наблюдения.
    Изчислява POD, FAR, CSI и MAE на видимостта.
    """
    import numpy as np
    from datetime import datetime, timedelta

    obs_list = all_obs.get(icao, [])

    print(f"\n{'═'*68}")
    print(f"  ВЕРИФИКАЦИЯ — {icao}  {date_str} {hour0:02d}UTC → +{len(history)-1}h")
    print(f"{'═'*68}")
    print(f"  {'UTC':>5} | {'VIS_mod':>8} | {'CAT_mod':>7} | "
          f"{'T_mod':>6} | {'VIS_obs':>8} | {'CAT_obs':>7} | {'T_obs':>6} | Резултат")
    print(f"  {'-'*75}")

    hits = misses = false_alarms = correct_neg = 0
    vis_errors = []

    base_dt = datetime.strptime(date_str, "%Y-%m-%d")

    for r in history:
        vis_mod = r["vis_sfc"]
        cat_mod = r["cat"]
        T_mod   = r["T_sfc"] - 273.15

        # Намираме наблюдението за този час
        obs_dt   = base_dt + timedelta(hours=hour0 + r["time_h"])
        obs_date = obs_dt.strftime("%Y-%m-%d")
        obs_h    = obs_dt.hour

        best_obs  = None
        best_diff = 35   # ±35 мин прозорец
        for obs in obs_list:
            t = obs["time"]
            m = re.search(r'T(\d{2}):(\d{2})', t)
            if not m or obs_date not in t:
                continue
            oh, omn = int(m[1]), int(m[2])
            diff = abs(oh * 60 + omn - obs_h * 60)
            if diff < best_diff:
                best_diff = diff
                best_obs  = obs

        if best_obs is None:
            print(f"  {r['hour_utc']:5.1f} | {vis_mod:8.0f} | {cat_mod:>7} | "
                  f"{T_mod:6.1f} | {'—':>8} | {'—':>7} | {'—':>6} | няма obs")
            continue

        vis_obs = best_obs.get("vis_m") or 10000
        T_obs   = best_obs.get("T")
        T_obs_s = f"{T_obs:.1f}" if T_obs is not None else "—"

        if vis_obs < 200:    cat_obs = "LIFR"
        elif vis_obs < 600:  cat_obs = "IFR"
        elif vis_obs < 1000: cat_obs = "MVFR"
        else:                cat_obs = "VFR"

        fog_mod = vis_mod < 1000
        fog_obs = vis_obs < 1000

        if   fog_mod and fog_obs:      hits += 1;         result = "✓ HIT"
        elif fog_mod and not fog_obs:  false_alarms += 1; result = "✗ FA"
        elif not fog_mod and fog_obs:  misses += 1;       result = "✗ MISS"
        else:                          correct_neg += 1;  result = "✓ CN"

        vis_errors.append(abs(vis_mod - vis_obs))

        print(f"  {r['hour_utc']:5.1f} | {vis_mod:8.0f} | {cat_mod:>7} | "
              f"{T_mod:6.1f} | {vis_obs:8.0f} | {cat_obs:>7} | {T_obs_s:>6} | {result}")

    # Метрики
    pod = hits / (hits + misses)              if (hits + misses) > 0          else float("nan")
    far = false_alarms / (hits+false_alarms)  if (hits + false_alarms) > 0   else float("nan")
    csi = hits / (hits+misses+false_alarms)   if (hits+misses+false_alarms)>0 else float("nan")
    mae = float(np.mean(vis_errors))          if vis_errors                   else float("nan")

    print(f"\n  Метрики (праг VIS < 1000m):")
    print(f"    POD : {pod:.2f}   FAR : {far:.2f}   CSI : {csi:.2f}   MAE : {mae:.0f}m")
    print(f"    Hits={hits}  Misses={misses}  FA={false_alarms}  CN={correct_neg}")
    print(f"{'═'*68}")

    return {"POD": pod, "FAR": far, "CSI": csi, "MAE": mae,
            "hits": hits, "misses": misses,
            "false_alarms": false_alarms, "correct_neg": correct_neg}


# ──────────────────────────────────────────────────────────────
# Тест
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    icao = sys.argv[1] if len(sys.argv) > 1 else "LBSF"
    date = sys.argv[2] if len(sys.argv) > 2 else "2024-01-15"
    hour = int(sys.argv[3]) if len(sys.argv) > 3 else 18

    obs = fetch_metar_ogimet(icao, date, hour0=hour, hours=14)
    print(f"\n{len(obs)} наблюдения:")
    for o in obs:
        wx = " ".join(o["weather"]) or "—"
        print(f"  {o['time']}  T={o['T']:5}°C  Td={o['Td']:5}°C  "
              f"VIS={str(o['vis_m']):>6}m  {wx}")
