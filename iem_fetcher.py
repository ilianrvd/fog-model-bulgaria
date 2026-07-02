"""
iem_fetcher.py
==============
Изтегля исторически METAR от Iowa State University IEM архив.
API: https://mesonet.agron.iastate.edu/api/1/obhistory.json

Предимства пред aviationweather.gov:
  - Архив от десетилетия назад
  - Пълна история за деня (всички METAR на 30/60 мин)
  - Стабилен JSON API без ключ
  - Верификационна функция hour-by-hour

Употреба
--------
from iem_fetcher import fetch_metar_iem, fetch_all_iem, verify_forecast

# Единично летище
metars = fetch_metar_iem("LBSF", "2024-01-15")

# Всички летища
all_m = fetch_all_iem(["LBSF","LBWN","LBBG","LBPD","LBGO"], "2024-01-15")

# Верификация
verify_forecast(history, "LBSF", "2024-01-15", hour0=18)
"""

import json
import time
import urllib.request
import urllib.parse
import re
import numpy as np
from datetime import datetime, timezone, timedelta

IEM_BASE = "https://mesonet.agron.iastate.edu/api/1/obhistory.json"


# ──────────────────────────────────────────────────────────────
# Основен fetcher
# ──────────────────────────────────────────────────────────────

def fetch_metar_iem(icao: str, date_str: str,
                    sleep_s: float = 0.3) -> list:
    """
    Изтегля всички METAR за станция и дата от IEM архива.

    Параметри
    ----------
    icao     : ICAO код (LBSF, LBWN, ...)
    date_str : дата 'YYYY-MM-DD'
    sleep_s  : пауза между заявките (учтивост към сървъра)

    Връща
    ------
    list of dict:
        {
            'time'    : 'YYYY-MM-DDTHH:MM:00Z',
            'raw'     : суров METAR низ,
            'T'       : температура [°C] или None,
            'Td'      : точка на росата [°C] или None,
            'vis_m'   : видимост [m] или None,
            'wind_dir': посока [°] или None,
            'wind_kt' : скорост [kt] или None,
            'weather' : списък явления,
        }
    """
    params = {
        "station" : icao,
        "date"    : date_str,
        "tz"      : "UTC",
    }
    url = IEM_BASE + "?" + urllib.parse.urlencode(params)

    print(f"[IEM] {icao} @ {date_str}  →  {url[:70]}...")

    req = urllib.request.Request(url, headers={
        "User-Agent" : "fog-model-dprvd/1.0 (aviation-met@bulatsa.bg)",
        "Accept"     : "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"IEM HTTP грешка: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"IEM мрежова грешка: {e.reason}")

    records = data.get("data", [])
    if not records:
        print(f"[IEM] {icao}: няма данни за {date_str}")
        return []

    result = []
    for rec in records:
        # IEM връща полета директно
        raw = rec.get("metar") or rec.get("raw_ob") or ""
        t_utc = rec.get("valid") or rec.get("utc_valid") or ""

        # Нормализираме времевия низ
        t_utc = t_utc.replace(" ", "T")
        if not t_utc.endswith("Z"):
            t_utc += "Z"

        # Декодираме директно от IEM полетата (по-надеждно от raw parsing)
        T_C   = rec.get("tmpf")    # °F
        Td_C  = rec.get("dwpf")    # °F
        vis_m = rec.get("vsby")    # miles
        ws_kt = rec.get("sknt")    # kt
        wd    = rec.get("drct")    # degrees
        wxcodes = rec.get("wxcodes") or rec.get("presentwx") or ""

        # Конверсии
        T_C  = (float(T_C)  - 32) * 5/9 if T_C  is not None else None
        Td_C = (float(Td_C) - 32) * 5/9 if Td_C is not None else None
        vis_m = float(vis_m) * 1609.34   if vis_m is not None else None
        vis_m = min(vis_m, 10000)         if vis_m is not None else None

        weather = []
        if wxcodes:
            weather = str(wxcodes).split()

        # Ако raw е наличен — използваме го за допълнителна информация
        if not raw and rec.get("station"):
            # Строим минимален raw низ
            raw = f"METAR {icao} {t_utc[:10].replace('-','')}Z"

        result.append({
            "time"     : t_utc,
            "raw"      : raw,
            "T"        : round(T_C, 1)   if T_C   is not None else None,
            "Td"       : round(Td_C, 1)  if Td_C  is not None else None,
            "vis_m"    : round(vis_m)    if vis_m  is not None else None,
            "wind_dir" : int(wd)          if wd    is not None else None,
            "wind_kt"  : int(ws_kt)       if ws_kt is not None else None,
            "weather"  : weather,
        })

    # Сортираме по време
    result.sort(key=lambda x: x["time"])
    print(f"[IEM] {icao}: {len(result)} наблюдения")
    if result:
        print(f"      Първо: {result[0]['time']}  T={result[0]['T']}°C")
        print(f"      Последно: {result[-1]['time']}  T={result[-1]['T']}°C")

    time.sleep(sleep_s)
    return result


# ──────────────────────────────────────────────────────────────
# Изтегляне за всички летища
# ──────────────────────────────────────────────────────────────

def fetch_all_iem(icao_list: list, date_str: str) -> dict:
    """
    Изтегля METAR за всички летища от IEM.

    Връща dict {icao: [list of obs dicts]}
    """
    result = {}
    for icao in icao_list:
        try:
            obs = fetch_metar_iem(icao, date_str)
            result[icao] = obs
        except Exception as e:
            print(f"[IEM] ⚠ {icao}: {e}")
            result[icao] = []
    return result


# ──────────────────────────────────────────────────────────────
# Намиране на METAR за даден час
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
    best     = None
    best_diff = window_min + 1
    best_h, best_mn = target_hour, 0

    for obs in obs_list:
        t = obs["time"]
        m = re.search(r'T(\d{2}):(\d{2})', t)
        if not m:
            continue
        h, mn = int(m[1]), int(m[2])
        # Само за правилната дата
        if date_str not in t:
            continue
        diff = abs(h * 60 + mn - target_min)
        if diff < best_diff:
            best_diff = diff
            best      = obs
            best_h, best_mn = h, mn

    if best:
        print(f"[IEM] {icao}: намерено @ {best_h:02d}:{best_mn:02d} UTC "
              f"(±{best_diff}min от {target_hour:02d}:00)")
    return best, best_h, best_mn


def obs_to_metar_dict(obs: dict) -> dict:
    """
    Конвертира IEM obs dict към формата, очакван от metar_parser.apply_metar_correction.
    """
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
# Верификация
# ──────────────────────────────────────────────────────────────

def verify_forecast(history: list, icao: str,
                    date_str: str, hour0: int,
                    all_obs: dict = None) -> dict:
    """
    Сравнява прогнозата с реалните METAR hour-by-hour.

    Параметри
    ----------
    history  : изход от FogModel1D.run()
    icao     : ICAO код
    date_str : дата на старта
    hour0    : UTC час на старта
    all_obs  : dict от fetch_all_iem (ако None — изтегля автоматично)

    Връща dict с верификационни метрики
    """
    if all_obs is None:
        # Изтегляме и следващия ден ако прогнозата минава полунощ
        obs1 = fetch_metar_iem(icao, date_str)
        dt_next = (datetime.strptime(date_str, "%Y-%m-%d")
                   + timedelta(days=1)).strftime("%Y-%m-%d")
        obs2 = fetch_metar_iem(icao, dt_next)
        all_obs = {icao: obs1 + obs2}

    obs_list = all_obs.get(icao, [])

    print(f"\n{'═'*65}")
    print(f"  ВЕРИФИКАЦИЯ — {icao}  {date_str} {hour0:02d} UTC → +{len(history)-1}h")
    print(f"{'═'*65}")
    print(f"  {'UTC':>5} | {'VIS_mod':>8} | {'CAT_mod':>7} | "
          f"{'VIS_obs':>8} | {'CAT_obs':>7} | {'Резултат':>10}")
    print(f"  {'-'*62}")

    hits = 0; misses = 0; false_alarms = 0; correct_neg = 0
    scores = []

    for r in history:
        h_utc  = r["hour_utc"]
        vis_mod = r["vis_sfc"]
        cat_mod = r["cat"]

        # Намираме съответното наблюдение
        h_int = int(h_utc)
        mn_int = int((h_utc - h_int) * 60)

        # Дата на наблюдението
        base_dt = datetime.strptime(date_str, "%Y-%m-%d")
        delta_h = r["time_h"]
        obs_dt  = base_dt + timedelta(hours=hour0 + delta_h)
        obs_date_str = obs_dt.strftime("%Y-%m-%d")
        obs_h   = obs_dt.hour

        best_obs = None
        best_diff = 45   # ±45 мин прозорец
        for obs in obs_list:
            t = obs["time"]
            m = re.search(r'T(\d{2}):(\d{2})', t)
            if not m:
                continue
            oh, omn = int(m[1]), int(m[2])
            if obs_date_str not in t:
                continue
            diff = abs(oh * 60 + omn - obs_h * 60)
            if diff < best_diff:
                best_diff = diff
                best_obs = obs

        if best_obs is None:
            print(f"  {h_utc:5.1f} | {vis_mod:8.0f} | {cat_mod:>7} | "
                  f"{'—':>8} | {'—':>7} | {'няма obs':>10}")
            continue

        vis_obs = best_obs.get("vis_m") or 10000
        # Категория от наблюдението
        if vis_obs < 200:    cat_obs = "LIFR"
        elif vis_obs < 600:  cat_obs = "IFR"
        elif vis_obs < 1000: cat_obs = "MVFR"
        else:                cat_obs = "VFR"

        # Верификация: прагове за мъгла (VIS < 1000m)
        fog_mod = vis_mod < 1000
        fog_obs = vis_obs < 1000

        if fog_mod and fog_obs:
            hits += 1;         result = "✓ HIT"
        elif fog_mod and not fog_obs:
            false_alarms += 1; result = "✗ FA"
        elif not fog_mod and fog_obs:
            misses += 1;       result = "✗ MISS"
        else:
            correct_neg += 1;  result = "✓ CN"

        scores.append(abs(vis_mod - vis_obs))

        print(f"  {h_utc:5.1f} | {vis_mod:8.0f} | {cat_mod:>7} | "
              f"{vis_obs:8.0f} | {cat_obs:>7} | {result:>10}")

    # Метрики
    total = hits + misses + false_alarms + correct_neg
    pod   = hits / (hits + misses)        if (hits + misses) > 0   else float('nan')
    far   = false_alarms / (hits + false_alarms) if (hits + false_alarms) > 0 else float('nan')
    csi   = hits / (hits + misses + false_alarms) if (hits + misses + false_alarms) > 0 else float('nan')
    mae   = float(np.mean(scores)) if scores else float('nan')

    print(f"\n  Метрики (праг VIS < 1000m):")
    print(f"    POD (Probability of Detection) : {pod:.2f}")
    print(f"    FAR (False Alarm Rate)         : {far:.2f}")
    print(f"    CSI (Critical Success Index)   : {csi:.2f}")
    print(f"    MAE видимост                   : {mae:.0f} m")
    print(f"    Hits={hits}  Misses={misses}  FA={false_alarms}  CN={correct_neg}")
    print(f"{'═'*65}")

    return {
        "POD": pod, "FAR": far, "CSI": csi, "MAE": mae,
        "hits": hits, "misses": misses,
        "false_alarms": false_alarms, "correct_neg": correct_neg,
    }


# ──────────────────────────────────────────────────────────────
# Тест
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    icao = sys.argv[1] if len(sys.argv) > 1 else "LBSF"
    date = sys.argv[2] if len(sys.argv) > 2 else "2024-01-15"
    obs  = fetch_metar_iem(icao, date)
    print(f"\n{len(obs)} наблюдения за {icao} @ {date}")
    for o in obs[:5]:
        print(f"  {o['time']}  T={o['T']}°C  Td={o['Td']}°C  "
              f"VIS={o['vis_m']}m  {o['weather']}")
