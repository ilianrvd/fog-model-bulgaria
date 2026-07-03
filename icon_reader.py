"""
icon_reader.py
==============
Изтегля вертикален профил от ICON-EU чрез Open-Meteo API (безплатно, без ключ)
и го конвертира във формата, очакван от FogModel1D.

Open-Meteo използва DWD ICON-EU данни, обновявани на всеки час.
Документация: https://open-meteo.com/en/docs/dwd-api

Налични нива в Open-Meteo ICON-EU (hPa):
  1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 700, 600, 500

За мъглен модел използваме: 1000, 975, 950, 925, 900, 875, 850 hPa
(~0–1500 m за Sofia; варира с елевацията)
"""

import json
import urllib.request
import urllib.parse
import numpy as np
from datetime import datetime, timezone

# ──────────────────────────────────────────────────────────────
# Координати на летищата
# ──────────────────────────────────────────────────────────────
AIRPORT_COORDS = {
    "LBSF": {"lat": 42.697, "lon": 23.406, "elev": 531,  "name": "София"},
    "LBWN": {"lat": 43.232, "lon": 27.825, "elev":  41,  "name": "Варна"},
    "LBBG": {"lat": 42.568, "lon": 27.515, "elev":  20,  "name": "Бургас"},
    "LBPD": {"lat": 42.068, "lon": 24.851, "elev": 155,  "name": "Пловдив"},
    "LBGO": {"lat": 43.151, "lon": 25.713, "elev":  85,  "name": "Г. Оряховица"},
}

# Нива за профила (hPa) — от земята нагоре
PRESSURE_LEVELS = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 700]

# Физически константи
Rd    = 287.05
g     = 9.81
kappa = 0.2857
eps_r = 0.622

# ──────────────────────────────────────────────────────────────
# Open-Meteo заявка
# ──────────────────────────────────────────────────────────────

def fetch_icon_eu(icao: str, forecast_hours: int = 13) -> dict:
    """
    Изтегля ICON-EU профил за летището от Open-Meteo.

    Параметри
    ----------
    icao           : ICAO код (LBSF, LBWN, LBBG, LBPD, LBGO)
    forecast_hours : брой часове за изтегляне (default 13 за 12h прогноза)

    Връща
    ------
    dict с ключове: z, T, qv, p, u, v, hour0, valid_time, hourly_profiles
    """
    if icao not in AIRPORT_COORDS:
        raise ValueError(f"Неизвестно летище: {icao}. "
                         f"Налични: {list(AIRPORT_COORDS.keys())}")

    coords  = AIRPORT_COORDS[icao]
    lat, lon = coords["lat"], coords["lon"]
    elev     = coords["elev"]

    # Списък с променливи за профила по нива
    level_vars = []
    for lev in PRESSURE_LEVELS:
        level_vars += [
            f"temperature_{lev}hPa",
            f"relativehumidity_{lev}hPa",
            f"geopotential_height_{lev}hPa",
            f"windspeed_{lev}hPa",
            f"winddirection_{lev}hPa",
        ]

    # Приземни променливи
    surface_vars = [
        "temperature_2m",
        "dewpoint_2m",
        "surface_pressure",
        "windspeed_10m",
        "winddirection_10m",
        "relativehumidity_2m",
    ]

    all_vars = surface_vars + level_vars

    params = {
        "latitude"     : lat,
        "longitude"    : lon,
        "hourly"       : ",".join(all_vars),
        "forecast_days": max(1, (forecast_hours // 24) + 1),
        "timezone"     : "UTC",
        "models"       : "icon_eu",
    }

    url = "https://api.open-meteo.com/v1/dwd-icon?" + urllib.parse.urlencode(params)

    print(f"[ICON-EU] Изтегляне на профил за {icao} ({coords['name']})...")
    print(f"[ICON-EU] URL: {url[:80]}...")

    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0 (aviation-met@bulatsa.bg)"
    })

    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            last_err = None
            break
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Open-Meteo HTTP грешка: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            last_err = e
            print(f"[ICON-EU] Опит {attempt+1}/3 неуспешен: {e.reason} — повтарям...")
            _time.sleep(5)
    if last_err:
        raise RuntimeError(f"Мрежова грешка: {last_err.reason}")

    hourly = data["hourly"]
    times  = hourly["time"]                    # ISO strings

    # Намери текущия час (или най-близкия)
    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%Y-%m-%dT%H:00")
    try:
        t0_idx = times.index(now_str)
    except ValueError:
        t0_idx = 0
        print(f"[ICON-EU] Предупреждение: {now_str} не е в данните, използвам t[0]={times[0]}")

    valid_time = times[t0_idx]
    hour0      = int(valid_time[11:13]) + int(valid_time[14:16]) / 60.0
    print(f"[ICON-EU] Валиден час: {valid_time} UTC  (hour0={hour0:.1f})")

    # ── Извличане на профила за t0_idx ──
    z_list   = []
    T_list   = []
    rh_list  = []
    p_list   = []
    ws_list  = []
    wd_list  = []

    sfc_p_hPa = hourly.get("surface_pressure", [101325/100]*len(times))[t0_idx] or 1013.25

    for lev in PRESSURE_LEVELS:
        p_hPa = float(lev)
        p_Pa  = p_hPa * 100.0

        # Пропускаме нива под земята (p > sfc_p)
        if p_hPa > sfc_p_hPa + 5:
            continue

        T_C   = hourly.get(f"temperature_{lev}hPa",       [None]*len(times))[t0_idx]
        rh    = hourly.get(f"relativehumidity_{lev}hPa",  [None]*len(times))[t0_idx]
        z_m   = hourly.get(f"geopotential_height_{lev}hPa",[None]*len(times))[t0_idx]
        ws    = hourly.get(f"windspeed_{lev}hPa",         [None]*len(times))[t0_idx]
        wd    = hourly.get(f"winddirection_{lev}hPa",     [None]*len(times))[t0_idx]

        if None in (T_C, rh, z_m):
            continue

        z_agl = max(float(z_m) - elev, 2.0)   # m AGL
        z_list.append(z_agl)
        T_list.append(float(T_C) + 273.15)
        rh_list.append(float(rh) / 100.0)
        p_list.append(p_Pa)
        ws_list.append(float(ws or 0))
        wd_list.append(float(wd or 0))

    if len(z_list) < 3:
        raise RuntimeError("Недостатъчно вертикални нива от Open-Meteo.")

    z   = np.array(z_list)
    T   = np.array(T_list)
    rh  = np.clip(np.array(rh_list), 0.0, 1.0)
    p   = np.array(p_list)
    ws  = np.array(ws_list) * (1000.0 / 3600.0)   # km/h → m/s
    wd  = np.array(wd_list)

    # Сортиране по z (нараства)
    idx = np.argsort(z)
    z, T, rh, p, ws, wd = z[idx], T[idx], rh[idx], p[idx], ws[idx], wd[idx]

    # Специфична влажност от RH
    es  = 611.2 * np.exp(17.67 * (T - 273.15) / (T - 273.15 + 243.5))
    qv  = eps_r * rh * es / (p - rh * es)
    qv  = np.maximum(qv, 1e-8)

    # Вятърни компоненти
    u = -ws * np.sin(np.deg2rad(wd))
    v = -ws * np.cos(np.deg2rad(wd))

    # Приземна корекция (2m данни са по-точни от 1000 hPa)
    T2m = hourly.get("temperature_2m",  [None]*len(times))[t0_idx]
    Td2m = hourly.get("dewpoint_2m",    [None]*len(times))[t0_idx]
    ws10 = hourly.get("windspeed_10m",  [None]*len(times))[t0_idx]
    wd10 = hourly.get("winddirection_10m",[None]*len(times))[t0_idx]

    if T2m is not None:
        weight = np.exp(-z / 80.0)
        T  += (float(T2m) + 273.15 - T[0]) * weight
        print(f"[ICON-EU] T_2m={T2m:.1f}°C  →  приземна корекция приложена")

    if Td2m is not None and T2m is not None:
        es_td = 611.2 * np.exp(17.67 * float(Td2m) / (float(Td2m) + 243.5))
        p_sfc = p[0]
        qv_sfc = eps_r * es_td / (p_sfc - es_td)
        weight = np.exp(-z / 80.0)
        qv += (qv_sfc - qv[0]) * weight
        qv  = np.maximum(qv, 1e-8)

    if ws10 is not None and wd10 is not None:
        ws10_ms = float(ws10) * (1000.0 / 3600.0)
        u10 = -ws10_ms * np.sin(np.deg2rad(float(wd10)))
        v10 = -ws10_ms * np.cos(np.deg2rad(float(wd10)))
        weight = np.exp(-z / 100.0)
        u += (u10 - u[0]) * weight
        v += (v10 - v[0]) * weight

    print(f"[ICON-EU] Извлечени {len(z)} нива  "
          f"(z={z[0]:.0f}–{z[-1]:.0f} m AGL)")
    print(f"[ICON-EU] T[0]={T[0]-273.15:.1f}°C  "
          f"qv[0]={qv[0]*1000:.2f} g/kg  "
          f"p[0]={p[0]/100:.0f} hPa")

    # Hourly profiles за nudging (следващите forecast_hours часа)
    hourly_profiles = []
    for ti in range(t0_idx, min(t0_idx + forecast_hours, len(times))):
        prof_t = _extract_profile_at(hourly, times, ti, PRESSURE_LEVELS, elev)
        if prof_t:
            hourly_profiles.append(prof_t)

    return {
        "z"               : z,
        "T"               : T,
        "qv"              : qv,
        "p"               : p,
        "u"               : u,
        "v"               : v,
        "hour0"           : hour0,
        "valid_time"      : valid_time,
        "icao"            : icao,
        "hourly_profiles" : hourly_profiles,   # за nudging
    }


def _extract_profile_at(hourly, times, ti, levels, elev):
    """Извлича профил за даден времеви индекс (за nudging)."""
    z_l, T_l, p_l, u_l, v_l, qv_l = [], [], [], [], [], []
    sfc_p = (hourly.get("surface_pressure", [1013]*len(times))[ti] or 1013)

    for lev in levels:
        if lev > sfc_p + 5:
            continue
        T_C = hourly.get(f"temperature_{lev}hPa",      [None]*len(times))[ti]
        rh  = hourly.get(f"relativehumidity_{lev}hPa", [None]*len(times))[ti]
        z_m = hourly.get(f"geopotential_height_{lev}hPa",[None]*len(times))[ti]
        ws  = hourly.get(f"windspeed_{lev}hPa",        [None]*len(times))[ti]
        wd  = hourly.get(f"winddirection_{lev}hPa",    [None]*len(times))[ti]
        if None in (T_C, rh, z_m):
            continue
        T_K  = float(T_C) + 273.15
        rh_f = float(rh) / 100.0
        p_Pa = lev * 100.0
        es   = 611.2 * np.exp(17.67*(T_K-273.15)/(T_K-273.15+243.5))
        qv   = max(0.622 * rh_f * es / (p_Pa - rh_f*es), 1e-8)
        ws_ms = float(ws or 0) * (1000/3600)
        wd_f  = float(wd or 0)
        z_list = max(float(z_m) - elev, 2.0)
        z_l.append(z_list); T_l.append(T_K); p_l.append(p_Pa)
        qv_l.append(qv)
        u_l.append(-ws_ms * np.sin(np.deg2rad(wd_f)))
        v_l.append(-ws_ms * np.cos(np.deg2rad(wd_f)))

    if len(z_l) < 3:
        return None
    idx = np.argsort(z_l)
    return {
        "time" : times[ti],
        "z"    : np.array(z_l)[idx],
        "T"    : np.array(T_l)[idx],
        "qv"   : np.array(qv_l)[idx],
        "p"    : np.array(p_l)[idx],
        "u"    : np.array(u_l)[idx],
        "v"    : np.array(v_l)[idx],
    }


# ──────────────────────────────────────────────────────────────
# Тест
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    icao = sys.argv[1] if len(sys.argv) > 1 else "LBSF"
    prof = fetch_icon_eu(icao)
    print(f"\nПрофил за {icao}:")
    print(f"  z (m AGL): {[f'{v:.0f}' for v in prof['z']]}")
    print(f"  T (°C)   : {[f'{v-273.15:.1f}' for v in prof['T']]}")
    print(f"  RH (%)   : {[f'{v*1000/0.622/(611.2*np.exp(17.67*(T-273.15)/(T-273.15+243.5))/p)*100:.0f}' for v,T,p in zip(prof['qv'],prof['T'],prof['p'])]}")
    print(f"  Nudging профили: {len(prof['hourly_profiles'])} часа")
