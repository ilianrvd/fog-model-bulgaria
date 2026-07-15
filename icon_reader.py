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
# Стандартни нива за api.open-meteo.com


# Детекция на средата
import os as _os
_IS_GITHUB_ACTIONS = _os.getenv("GITHUB_ACTIONS") == "true"
if _IS_GITHUB_ACTIONS:
    _ICON_BASE_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    print("[ICON-EU] Среда: GitHub Actions → historical-forecast-api")
else:
    _ICON_BASE_URL = "https://api.open-meteo.com/v1/dwd-icon"

PRESSURE_LEVELS_FULL     = [1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 700]
# Ensemble-api поддържа по-малко нива
PRESSURE_LEVELS_ENSEMBLE = [1000, 925, 850, 700, 500]

# Избираме по среда
PRESSURE_LEVELS = PRESSURE_LEVELS_ENSEMBLE if _IS_GITHUB_ACTIONS else PRESSURE_LEVELS_FULL

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
        "soil_temperature_0cm",   # за Force-Restore soil flux
        "cloudcover", "cloudcover_low", "cloudcover_mid", "cloudcover_high",
        "precipitation",
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

    # URL зависи от средата
    params["models"] = "icon_eu"
    if _IS_GITHUB_ACTIONS:
        # historical-forecast-api изисква start_date/end_date
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        today    = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        tomorrow = (_dt.now(_tz.utc) + _td(days=1)).strftime("%Y-%m-%d")
        params["start_date"] = today
        params["end_date"]   = tomorrow
    url = _ICON_BASE_URL + "?" + urllib.parse.urlencode(params)

    print(f"[ICON-EU] Изтегляне на профил за {icao} ({coords['name']})...")
    print(f"[ICON-EU] URL: {url[:80]}...")

    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0 (aviation-met@bulatsa.bg)"
    })

    import time as _time
    last_err = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
            last_err = None
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"[ICON-EU] 429 Too Many Requests — изчаквам {wait}s...")
                _time.sleep(wait)
                continue
            raise RuntimeError(f"Open-Meteo HTTP грешка: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            last_err = e
            wait = 15 * (attempt + 1)
            print(f"[ICON-EU] Опит {attempt+1}/5 неуспешен: {e.reason} — изчаквам {wait}s...")
            _time.sleep(wait)
    if last_err:
        raise RuntimeError(f"Мрежова грешка: {last_err.reason}")

    hourly = data["hourly"]
    times  = hourly["time"]                    # ISO strings


    # Helper за 2D ensemble данни (ensemble-api връща [member][час])
    def _get(field, ti):
        val = hourly.get(field)
        if val is None:
            return None
        if isinstance(val[0], list):
            return val[0][ti] if ti < len(val[0]) else None
        return val[ti] if ti < len(val) else None

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

    sfc_p_hPa = _get("surface_pressure", t0_idx) or 1013.25

    for lev in PRESSURE_LEVELS:
        p_hPa = float(lev)
        p_Pa  = p_hPa * 100.0

        # Пропускаме нива под земята (p > sfc_p)
        if p_hPa > sfc_p_hPa + 5:
            continue

        T_C   = _get(f"temperature_{lev}hPa", t0_idx)
        rh    = _get(f"relativehumidity_{lev}hPa", t0_idx)
        z_m   = _get(f"geopotential_height_{lev}hPa", t0_idx)
        ws    = _get(f"windspeed_{lev}hPa", t0_idx)
        wd    = _get(f"winddirection_{lev}hPa", t0_idx)

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
    T2m = _get("temperature_2m", t0_idx)
    Td2m = _get("dewpoint_2m", t0_idx)
    ws10 = _get("windspeed_10m", t0_idx)
    wd10 = _get("winddirection_10m", t0_idx)

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

    # Почвена температура за soil flux
    T_soil_0cm = hourly.get("soil_temperature_0cm", [None]*len(times))[t0_idx]
    if T_soil_0cm is not None:
        T_soil_K = float(T_soil_0cm) + 273.15
        print(f"[ICON-EU] T_soil_0cm={T_soil_0cm:.1f}°C")
    else:
        T_soil_K = None

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

    # Ефективна облачност (0-1) по час — Crawford & Duchon тежести
    def _cf_at(ti):
        def g(name):
            arr = hourly.get(name)
            v = arr[ti] if arr is not None and ti < len(arr) else None
            return None if v is None else min(max(float(v)/100.0, 0.0), 1.0)
        lo, mi, hi, tot = (g("cloudcover_low"), g("cloudcover_mid"),
                           g("cloudcover_high"), g("cloudcover"))
        rh2 = g("relativehumidity_2m")
        rh2 = rh2 if rh2 is not None else 0.0
        pr  = g("precipitation", scale=None)
        pr  = pr if pr is not None else 0.0
        if lo is None and mi is None and hi is None:
            return (tot if tot is not None else 0.0, 0.0, 0.0, rh2, pr)
        return (lo or 0.0, mi or 0.0, hi or 0.0, rh2, pr)
    cc_series = [_cf_at(ti)
                 for ti in range(t0_idx,
                                 min(t0_idx + forecast_hours, len(times)))]

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
        "T_soil"          : T_soil_K,          # за Force-Restore soil flux
        "cc_series"       : cc_series,          # (lo,mid,hi) по час за SEB
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
        if None in (T_C, rh):
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


# ──────────────────────────────────────────────────────────────
# Multi-location fetch — една заявка за всичките 5 летища
# ──────────────────────────────────────────────────────────────

def fetch_icon_eu_all(icao_list: list, forecast_hours: int = 13) -> dict:
    """
    Изтегля ICON-EU профили за всичките летища с ЕДНА заявка.
    Избягва 429 rate limit при ensemble-api.

    Връща dict {icao: profile_dict}
    """
    # Координати в правилен ред
    coords_list = [(icao, AIRPORT_COORDS[icao]) for icao in icao_list
                   if icao in AIRPORT_COORDS]

    lats = ",".join(str(c["lat"]) for _, c in coords_list)
    lons = ",".join(str(c["lon"]) for _, c in coords_list)

    level_vars = []
    for lev in PRESSURE_LEVELS:
        level_vars += [
            f"temperature_{lev}hPa",
            f"relativehumidity_{lev}hPa",
            f"geopotential_height_{lev}hPa",
            f"windspeed_{lev}hPa",
            f"winddirection_{lev}hPa",
        ]

    surface_vars = [
        "temperature_2m", "dewpoint_2m", "surface_pressure",
        "windspeed_10m", "winddirection_10m",
    ]

    params = {
        "latitude"     : lats,
        "longitude"    : lons,
        "hourly"       : ",".join(surface_vars + level_vars),
        "forecast_days": max(1, forecast_hours // 24 + 1),
        "timezone"     : "UTC",
        "models"       : "icon_eu",
    }

    url = _ICON_BASE_URL + "?" + urllib.parse.urlencode(params)
    print(f"[ICON-EU ALL] {len(coords_list)} летища, 1 заявка")
    print(f"[ICON-EU ALL] URL: {url[:80]}...")

    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0"
    })

    import time as _t
    last_err = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data_list = json.loads(resp.read())
            last_err = None
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"[ICON-EU ALL] 429 — изчаквам {wait}s...")
                _t.sleep(wait)
                continue
            raise RuntimeError(f"Open-Meteo HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            last_err = e
            wait = 15 * (attempt + 1)
            print(f"[ICON-EU ALL] Опит {attempt+1}/5: {e.reason} — изчаквам {wait}s...")
            _t.sleep(wait)
    if last_err:
        raise RuntimeError(f"Мрежова грешка: {last_err.reason}")

    if not isinstance(data_list, list):
        data_list = [data_list]

    # Debug — какви полета са налични
    if data_list:
        hourly0 = data_list[0].get("hourly", {})
        print(f"[DEBUG] Налични hourly полета: {list(hourly0.keys())[:15]}")
        # Проверяваме geopotential_height за всяко ниво
        for lev in PRESSURE_LEVELS:
            key = f"geopotential_height_{lev}hPa"
            val = hourly0.get(key)
            if val is not None:
                v0 = val[0][0] if isinstance(val[0], list) else val[0]
                print(f"[DEBUG]   {key}: {v0}")
            else:
                print(f"[DEBUG]   {key}: ЛИПСВА")

    results = {}
    for i, (icao, coords) in enumerate(coords_list):
        if i >= len(data_list):
            print(f"[ICON-EU ALL] ⚠ Няма данни за {icao}")
            continue
        try:
            data    = data_list[i]
            hourly  = data["hourly"]
            times   = hourly["time"]
            elev    = coords["elev"]

            # Helper за 2D ensemble данни
            def _get(field, ti):
                val = hourly.get(field)
                if val is None:
                    return None
                if isinstance(val[0], list):
                    return val[0][ti] if ti < len(val[0]) else None
                return val[ti] if ti < len(val) else None

            # Намери текущия час
            now_utc = datetime.now(timezone.utc)
            now_str = now_utc.strftime("%Y-%m-%dT%H:00")
            try:
                t0_idx = times.index(now_str)
            except ValueError:
                t0_idx = 0

            valid_time = times[t0_idx]
            hour0      = int(valid_time[11:13]) + int(valid_time[14:16]) / 60.0

            # Извличаме профила
            z_l, T_l, p_l, u_l, v_l, qv_l = [], [], [], [], [], []
            sfc_p = _get("surface_pressure", t0_idx) or 1013.25

            for lev in PRESSURE_LEVELS:
                if lev > sfc_p + 5:
                    continue
                T_C = _get(f"temperature_{lev}hPa",        t0_idx)
                rh  = _get(f"relativehumidity_{lev}hPa",   t0_idx)
                z_m = _get(f"geopotential_height_{lev}hPa", t0_idx)
                ws  = _get(f"windspeed_{lev}hPa",          t0_idx)
                wd  = _get(f"winddirection_{lev}hPa",      t0_idx)
                if None in (T_C, rh, z_m):
                    continue
                T_K  = float(T_C) + 273.15
                rh_f = np.clip(float(rh)/100., 0., 1.)
                p_Pa = lev * 100.
                es   = 611.2 * np.exp(17.67*(T_K-273.15)/(T_K-273.15+243.5))
                qv   = max(eps_r * rh_f * es / (p_Pa - rh_f*es), 1e-8)
                ws_ms = float(ws or 0) * (1000/3600)
                wd_f  = float(wd or 0)
                z_agl = max(float(z_m) - elev, 2.)
                z_l.append(z_agl); T_l.append(T_K); p_l.append(p_Pa)
                qv_l.append(qv)
                u_l.append(-ws_ms * np.sin(np.deg2rad(wd_f)))
                v_l.append(-ws_ms * np.cos(np.deg2rad(wd_f)))

            if len(z_l) < 3:
                print(f"[ICON-EU ALL] ⚠ {icao}: само {len(z_l)} нива")
                continue

            idx = np.argsort(z_l)
            z   = np.array(z_l)[idx]; T   = np.array(T_l)[idx]
            qv  = np.array(qv_l)[idx]; p   = np.array(p_l)[idx]
            u   = np.array(u_l)[idx];  v   = np.array(v_l)[idx]

            # Приземна корекция
            T2m  = _get("temperature_2m",    t0_idx)
            Td2m = _get("dewpoint_2m",       t0_idx)
            ws10 = _get("windspeed_10m",     t0_idx)
            wd10 = _get("winddirection_10m", t0_idx)

            if T2m is not None:
                w = np.exp(-z/80.); T += (float(T2m)+273.15 - T[0]) * w
            if Td2m is not None:
                es_td = 611.2*np.exp(17.67*float(Td2m)/(float(Td2m)+243.5))
                qv0   = eps_r*es_td/(p[0]-es_td)
                w = np.exp(-z/80.); qv += (qv0-qv[0])*w; qv = np.maximum(qv, 1e-8)
            if ws10 is not None and wd10 is not None:
                ws_ms = float(ws10)*(1000/3600)
                u10 = -ws_ms*np.sin(np.deg2rad(float(wd10)))
                v10 = -ws_ms*np.cos(np.deg2rad(float(wd10)))
                w = np.exp(-z/100.); u += (u10-u[0])*w; v += (v10-v[0])*w

            # Hourly профили за nudging
            hourly_profiles = []
            for ti in range(t0_idx, min(t0_idx+forecast_hours+1, len(times))):
                def _get_ti(field, ti=ti):
                    val = hourly.get(field)
                    if val is None: return None
                    if isinstance(val[0], list): return val[0][ti] if ti < len(val[0]) else None
                    return val[ti] if ti < len(val) else None
                z_l2,T_l2,p_l2,u_l2,v_l2,qv_l2 = [],[],[],[],[],[]
                sfc_p_t = _get_ti("surface_pressure") or 1013.25
                for lev in PRESSURE_LEVELS:
                    if lev > sfc_p_t + 5: continue
                    T_C2 = _get_ti(f"temperature_{lev}hPa")
                    rh2  = _get_ti(f"relativehumidity_{lev}hPa")
                    z_m2 = _get_ti(f"geopotential_height_{lev}hPa")
                    ws2  = _get_ti(f"windspeed_{lev}hPa")
                    wd2  = _get_ti(f"winddirection_{lev}hPa")
                    if None in (T_C2, rh2, z_m2): continue
                    T_K2 = float(T_C2)+273.15; rh_f2 = np.clip(float(rh2)/100.,0.,1.)
                    p_Pa2 = lev*100.
                    es2 = 611.2*np.exp(17.67*(T_K2-273.15)/(T_K2-273.15+243.5))
                    qv2 = max(eps_r*rh_f2*es2/(p_Pa2-rh_f2*es2), 1e-8)
                    ws_ms2 = float(ws2 or 0)*(1000/3600); wd_f2 = float(wd2 or 0)
                    z_agl2 = max(float(z_m2)-elev, 2.)
                    z_l2.append(z_agl2); T_l2.append(T_K2); p_l2.append(p_Pa2)
                    qv_l2.append(qv2)
                    u_l2.append(-ws_ms2*np.sin(np.deg2rad(wd_f2)))
                    v_l2.append(-ws_ms2*np.cos(np.deg2rad(wd_f2)))
                if len(z_l2) >= 3:
                    idx2 = np.argsort(z_l2)
                    hourly_profiles.append({
                        "time": times[ti],
                        "z": np.array(z_l2)[idx2], "T": np.array(T_l2)[idx2],
                        "qv": np.array(qv_l2)[idx2], "p": np.array(p_l2)[idx2],
                        "u": np.array(u_l2)[idx2],  "v": np.array(v_l2)[idx2],
                    })

            print(f"[ICON-EU ALL] {icao}: {len(z)} нива  T[0]={T[0]-273.15:.1f}°C")
            results[icao] = {
                "z": z, "T": T, "qv": qv, "p": p, "u": u, "v": v,
                "hour0": hour0, "valid_time": valid_time, "icao": icao,
                "hourly_profiles": hourly_profiles,
            }
        except Exception as e:
            print(f"[ICON-EU ALL] ⚠ {icao}: {e}")

    return results
