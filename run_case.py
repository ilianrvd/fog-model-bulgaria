"""
run_case.py
===========
Ретроспективен анализ на мъглен случай с исторически ICON-EU данни.
Използва Open-Meteo Historical Forecast API (безплатно, без ключ).

Употреба
--------
python run_case.py --date 2026-07-01 --hour 18                        # всички 5
python run_case.py --date 2026-07-01 --hour 18 --airports LBSF LBGO   # само избрани
python run_case.py --date 2026-07-01 --hour 18                        # всички 5
python run_case.py --date 2026-07-01 --hour 18 --hours 12 --no-nudge
"""

import sys, os, argparse, json, urllib.request, urllib.parse
import numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from fog_model     import FogModel1D
from icon_reader   import AIRPORT_COORDS, PRESSURE_LEVELS, eps_r
from metar_parser  import parse_metar, apply_metar_correction
from output        import save_surface_csv, plot_forecast, print_summary_report

AIRPORT_CONFIG = {
    "LBSF": {"coastal": False, "N_d": 300e6, "tau_T": 3600,  "tau_qv": 10800, "sst_month": None},
    "LBWN": {"coastal": True,  "N_d":  50e6, "tau_T": 3600,  "tau_qv": 3600,  "sst_month": None},
    "LBBG": {"coastal": True,  "N_d":  80e6, "tau_T": 3600,  "tau_qv": 3600,  "sst_month": None},
    "LBPD": {"coastal": False, "N_d": 200e6, "tau_T": 3600,  "tau_qv": 10800, "sst_month": None},
    "LBGO": {"coastal": False, "N_d": 150e6, "tau_T": 3600,  "tau_qv": 10800, "sst_month": None},
}

# SST климатология за Черно море при Варна/Бургас [°C] по месец (1=яну ... 12=дек)
BLACK_SEA_SST = {1:7, 2:7, 3:8, 4:11, 5:16, 6:21, 7:24, 8:25, 9:22, 10:18, 11:13, 12:9}

def get_sst(date_str: str) -> float:
    """Връща SST [°C] по месец от климатологията."""
    month = int(date_str[5:7])
    return float(BLACK_SEA_SST[month])

# ──────────────────────────────────────────────────────────────
# Historical Forecast API
# ──────────────────────────────────────────────────────────────

def fetch_icon_historical(icao: str, date_str: str, hour0: int,
                          forecast_hours: int = 13) -> dict:
    """
    Изтегля исторически ICON-EU профил от Open-Meteo.

    Параметри
    ----------
    icao          : ICAO код
    date_str      : дата 'YYYY-MM-DD'
    hour0         : начален UTC час (0-23)
    forecast_hours: брой часове напред
    """
    coords = AIRPORT_COORDS[icao]
    lat, lon, elev = coords["lat"], coords["lon"], coords["elev"]

    # Нивови променливи
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
        "windspeed_10m",  "winddirection_10m", "relativehumidity_2m",
        "soil_temperature_0cm",   # за Force-Restore soil flux
    ]

    # Крайна дата (може да е следващия ден)
    dt_start = datetime.strptime(date_str, "%Y-%m-%d")
    dt_end   = dt_start + timedelta(days=1)
    end_str  = dt_end.strftime("%Y-%m-%d")

    params = {
        "latitude"    : lat,
        "longitude"   : lon,
        "hourly"      : ",".join(surface_vars + level_vars),
        "start_date"  : date_str,
        "end_date"    : end_str,
        "timezone"    : "UTC",
        "models"      : "icon_eu",
    }

    # Historical Forecast API endpoint
    url = ("https://historical-forecast-api.open-meteo.com/v1/forecast?"
           + urllib.parse.urlencode(params))

    print(f"[ICON-EU HIST] {icao} {date_str} {hour0:02d}UTC")
    print(f"[ICON-EU HIST] URL: {url[:80]}...")

    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0 (aviation-met@bulatsa.bg)"
    })

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Open-Meteo HTTP грешка: {e.code} — "
                           f"Историческите данни може да не са налични за тази дата.")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Мрежова грешка: {e.reason}")

    hourly = data["hourly"]
    times  = hourly["time"]

    # Намери t0 индекса
    t0_str = f"{date_str}T{hour0:02d}:00"
    try:
        t0_idx = times.index(t0_str)
    except ValueError:
        raise RuntimeError(f"Часът {t0_str} не е намерен в данните. "
                           f"Налични часове: {times[:5]}...{times[-3:]}")

    print(f"[ICON-EU HIST] Намерен час: {times[t0_idx]}  (idx={t0_idx})")

    # Извличаме профил за t0
    profile = _extract_single_profile(hourly, times, t0_idx, PRESSURE_LEVELS, elev)
    if profile is None:
        raise RuntimeError("Недостатъчно нива в профила.")

    # Приземна корекция с 2m данни
    T2m  = hourly.get("temperature_2m",    [None]*len(times))[t0_idx]
    Td2m = hourly.get("dewpoint_2m",       [None]*len(times))[t0_idx]
    ws10 = hourly.get("windspeed_10m",     [None]*len(times))[t0_idx]
    wd10 = hourly.get("winddirection_10m", [None]*len(times))[t0_idx]

    z   = profile["z"]
    T   = profile["T"]
    qv  = profile["qv"]
    p   = profile["p"]
    u   = profile["u"]
    v   = profile["v"]

    if T2m is not None:
        w = np.exp(-z / 80.0)
        T += (float(T2m) + 273.15 - T[0]) * w
        print(f"[ICON-EU HIST] T_2m={T2m:.1f}°C")

    if Td2m is not None:
        es_td = 611.2 * np.exp(17.67*float(Td2m)/(float(Td2m)+243.5))
        qv0   = eps_r * es_td / (p[0] - es_td)
        w = np.exp(-z / 80.0)
        qv += (qv0 - qv[0]) * w
        qv  = np.maximum(qv, 1e-8)

    if ws10 is not None and wd10 is not None:
        ws_ms = float(ws10) * (1000/3600)
        u10   = -ws_ms * np.sin(np.deg2rad(float(wd10)))
        v10   = -ws_ms * np.cos(np.deg2rad(float(wd10)))
        w = np.exp(-z / 100.0)
        u += (u10 - u[0]) * w
        v += (v10 - v[0]) * w

    # Почвена температура
    T_soil_val = hourly.get("soil_temperature_0cm", [None]*len(times))[t0_idx]
    T_soil_K   = (float(T_soil_val) + 273.15) if T_soil_val is not None else None
    if T_soil_K:
        print(f"[ICON-EU HIST] T_soil_0cm={T_soil_val:.1f}°C")

    print(f"[ICON-EU HIST] {len(z)} нива  T[0]={T[0]-273.15:.1f}°C  "
          f"qv[0]={qv[0]*1000:.2f} g/kg  p[0]={p[0]/100:.0f} hPa")

    # Hourly профили за nudging
    hourly_profiles = []
    for ti in range(t0_idx, min(t0_idx + forecast_hours + 1, len(times))):
        prof_t = _extract_single_profile(hourly, times, ti, PRESSURE_LEVELS, elev)
        if prof_t:
            # Приземна корекция за всеки час
            T2m_t  = hourly.get("temperature_2m",    [None]*len(times))[ti]
            Td2m_t = hourly.get("dewpoint_2m",       [None]*len(times))[ti]
            ws10_t = hourly.get("windspeed_10m",     [None]*len(times))[ti]
            wd10_t = hourly.get("winddirection_10m", [None]*len(times))[ti]
            zt = prof_t["z"]
            if T2m_t is not None:
                wt = np.exp(-zt/80.)
                prof_t["T"] += (float(T2m_t)+273.15 - prof_t["T"][0]) * wt
            if Td2m_t is not None:
                es_t = 611.2*np.exp(17.67*float(Td2m_t)/(float(Td2m_t)+243.5))
                qv0t = eps_r*es_t/(prof_t["p"][0]-es_t)
                wt   = np.exp(-zt/80.)
                prof_t["qv"] += (qv0t - prof_t["qv"][0]) * wt
                prof_t["qv"]  = np.maximum(prof_t["qv"], 1e-8)
            hourly_profiles.append(prof_t)

    print(f"[ICON-EU HIST] Nudging профили: {len(hourly_profiles)} часа")

    return {
        "z"               : z,
        "T"               : T,
        "qv"              : qv,
        "p"               : p,
        "u"               : u,
        "v"               : v,
        "hour0"           : float(hour0),
        "valid_time"      : t0_str,
        "icao"            : icao,
        "hourly_profiles" : hourly_profiles,
        "T_soil"          : T_soil_K,
    }


def _extract_single_profile(hourly, times, ti, levels, elev):
    """Извлича вертикален профил за един времеви индекс."""
    z_l, T_l, p_l, u_l, v_l, qv_l = [], [], [], [], [], []
    sfc_p = (hourly.get("surface_pressure", [1013]*len(times))[ti] or 1013)

    for lev in levels:
        if lev > sfc_p + 5:
            continue
        T_C = hourly.get(f"temperature_{lev}hPa",       [None]*len(times))[ti]
        rh  = hourly.get(f"relativehumidity_{lev}hPa",  [None]*len(times))[ti]
        z_m = hourly.get(f"geopotential_height_{lev}hPa",[None]*len(times))[ti]
        ws  = hourly.get(f"windspeed_{lev}hPa",         [None]*len(times))[ti]
        wd  = hourly.get(f"winddirection_{lev}hPa",     [None]*len(times))[ti]

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
        return None

    idx = np.argsort(z_l)
    return {
        "time": times[ti],
        "z"   : np.array(z_l)[idx],
        "T"   : np.array(T_l)[idx],
        "qv"  : np.array(qv_l)[idx],
        "p"   : np.array(p_l)[idx],
        "u"   : np.array(u_l)[idx],
        "v"   : np.array(v_l)[idx],
    }


# ──────────────────────────────────────────────────────────────
# Исторически METAR
# ──────────────────────────────────────────────────────────────

def fetch_metar_historical(icao_list: list, hours_back: int = 14) -> dict:
    """
    Изтегля METAR за последните hours_back часа.
    Връща dict {icao: [raw_str, ...]} — всички наблюдения.
    """
    ids_str = ",".join(icao_list)
    url = (f"https://aviationweather.gov/api/data/metar?"
           f"ids={ids_str}&format=json&hours={hours_back}")

    print(f"\n[METAR HIST] Изтегляне за {ids_str} (последните {hours_back}h)...")
    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0",
        "Accept"    : "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[METAR HIST] Грешка: {e}")
        return {}

    # Групираме по ICAO, сортирани по време
    result = {}
    for rec in data:
        icao = rec.get("icaoId") or rec.get("station_id", "")
        raw  = rec.get("rawOb")  or rec.get("raw_text", "")
        t    = rec.get("reportTime") or rec.get("obsTime", "")
        if icao and raw:
            result.setdefault(icao, []).append({"raw": raw, "time": str(t)})

    for icao in icao_list:
        obs = result.get(icao, [])
        print(f"[METAR HIST] {icao}: {len(obs)} наблюдения")
        for o in obs[:3]:   # покажи първите 3
            print(f"    {o['time']}  {o['raw']}")

    return result


def find_metar_at(all_obs: dict, icao: str, target_hour: int,
                  target_date: str) -> tuple:
    """
    Намира METAR най-близо до target_hour UTC на target_date.

    Връща: (raw_metar: str | None, actual_hour: int, actual_minute: int)
    — реалният час на намерения METAR, за да стартираме модела от него.
    """
    import re
    obs = all_obs.get(icao, [])
    target_min_total = target_hour * 60
    best_raw  = None
    best_diff = 91   # максимален прозорец ±90 мин
    best_h, best_mn = target_hour, 0

    for o in obs:
        t = o["time"]
        if target_date not in t:
            continue
        m = re.search(r'T(\d{2}):(\d{2})', t)
        if not m:
            continue
        h, mn = int(m[1]), int(m[2])
        diff = abs(h * 60 + mn - target_min_total)
        if diff < best_diff:
            best_diff = diff
            best_raw  = o["raw"]
            best_h, best_mn = h, mn

    if best_raw:
        print(f"[METAR] Намерен @ {best_h:02d}:{best_mn:02d} UTC  "
              f"(±{best_diff}min от заявения {target_hour:02d}:00)")
    return best_raw, best_h, best_mn



# ──────────────────────────────────────────────────────────────
# Диагностика на синоптичния режим
# ──────────────────────────────────────────────────────────────

def compute_forecast_changes(hourly_profiles: list, hours: int = 6) -> tuple:
    """
    Изчислява максималните промени в следващите `hours` часа
    от hourly ICON профилите.

    Връща: (dT850, dV_wind, dT_col, dqv_sfc)
      dT850   : макс. промяна на T @ ~850 hPa [K]
      dV_wind : макс. промяна на приземния вятър [m/s]
      dT_col  : макс. средна промяна на цялата колона [K]
      dqv_sfc : макс. промяна на qv @ z=0 [g/kg]
    """
    if not hourly_profiles:
        return 0.0, 0.0, 0.0, 0.0

    n = min(hours, len(hourly_profiles) - 1)
    p0 = hourly_profiles[0]

    dT850_max  = 0.0
    dV_max     = 0.0
    dT_col_max = 0.0
    dqv_max    = 0.0

    # Намираме индекса на ~850 hPa в профила (z ~ 1500m AGL)
    z0 = p0["z"]
    idx_850 = int(np.searchsorted(z0, 1500))
    idx_850 = min(idx_850, len(z0) - 1)

    # Приземен вятър от t=0
    ws0 = np.sqrt(p0["u"][0]**2 + p0["v"][0]**2)
    T850_0  = p0["T"][idx_850]
    T_col_0 = np.mean(p0["T"])
    qv0     = p0["qv"][0] * 1000.0   # g/kg

    for i in range(1, n + 1):
        pi = hourly_profiles[i]

        # Интерполираме на същата мрежа ако нивата се различават
        zi = pi["z"]
        idx_850i = min(int(np.searchsorted(zi, 1500)), len(zi)-1)

        dT850  = abs(float(pi["T"][idx_850i]) - float(T850_0))
        ws_i   = np.sqrt(pi["u"][0]**2 + pi["v"][0]**2)
        dV     = abs(float(ws_i) - float(ws0))
        dT_col = abs(float(np.mean(pi["T"])) - float(T_col_0))
        dqv    = abs(float(pi["qv"][0]) * 1000.0 - float(qv0))

        dT850_max  = max(dT850_max,  dT850)
        dV_max     = max(dV_max,     dV)
        dT_col_max = max(dT_col_max, dT_col)
        dqv_max    = max(dqv_max,    dqv)

    return dT850_max, dV_max, dT_col_max, dqv_max


def diagnose_regime(profile: dict, metar: dict, cfg: dict) -> tuple:
    """
    Определя синоптичния режим от текущото състояние И прогнозните профили.

    Сигнали (OR логика с йерархия):
      DYNAMIC  : ΔT850 > 2K  OR  ΔV_wind > 5 m/s         (силен)
      DYNAMIC  : ΔT_колона > 1.5K                          (среден)
      MODERATE : Δqv > 1 g/kg  OR  крайбрежно + V > 3m/s  (слаб)
      RADIATIVE: нищо от горните → без nudging

    Връща: (режим: str, tau: int|None, причина: str)
    """
    # Приземен вятър от METAR
    V_sfc = (metar.get("wind_speed") or 0) * 0.5144   # kt → m/s

    # Промени в следващите 6h от ICON
    hourly = profile.get("hourly_profiles", [])
    dT850, dV_wind, dT_col, dqv = compute_forecast_changes(hourly, hours=6)

    print(f"[РЕЖИМ] Сигнали: ΔT850={dT850:.1f}K  ΔV={dV_wind:.1f}m/s  "
          f"V_sfc={V_sfc:.1f}m/s")

    # ── Синоптични сигнали → DYNAMIC ──
    # Само T850 и вятър — надеждни индикатори за синоптична промяна.

    # Текущ силен вятър → радиационна мъгла е невъзможна
    if V_sfc > 4.0:
        return "dynamic", 10800, f"Текущ V={V_sfc:.1f}m/s > 4m/s → радиационна мъгла невъзможна"

    # Р3: Decoupling gate — при силна котловинна инверсия ΔT850 не стига долу
    # Инверсията изолира котловината от свободната атмосфера
    # Използваме profile["T"] и profile["z"] — оригиналните ICON нива (преди build_surface_layer)
    inversion_strength = 0.0
    if metar.get("T") is not None:
        T_sfc_metar = float(metar["T"]) + 273.15
        # Вземаме T и z на второто ICON ниво (z[1]) — след build_surface_layer z[0]=2m
        # Търсим ниво над 50m AGL за надежден инверсионен сигнал
        prof_z = profile.get("z", [])
        prof_T = profile.get("T", [])
        # Търсим първото ниво между 100-300m AGL — там инверсията е най-видима
        for iz in range(len(prof_z)):
            if float(prof_z[iz]) > 100.0:
                z_icon_0 = float(prof_z[iz])
                T_icon_0 = float(prof_T[iz])
                inversion_strength = (T_icon_0 - T_sfc_metar) / z_icon_0 * 100  # K/100m
                print(f"[DECOUPLING] T_sfc={T_sfc_metar-273.15:.1f}°C  T_icon={T_icon_0-273.15:.1f}°C  z={z_icon_0:.0f}m  inv={inversion_strength:.1f}K/100m")
                break

    # Ако инверсията е силна (>2K/100m) и вятърът е тих — котловината е откачена
    decoupled = (inversion_strength > 1.0 and V_sfc < 3.0)

    # Адвекция на въздушна маса на 850 hPa — само ако не е decoupled
    if dT850 > 2.0 and not decoupled:
        return "dynamic", 3600, f"ΔT850={dT850:.1f}K > 2K → адвекция на въздушна маса"
    elif dT850 > 2.0 and decoupled:
        print(f"[РЕЖИМ] ΔT850={dT850:.1f}K но инверсия {inversion_strength:.1f}K/100m + V={V_sfc:.1f}m/s → котловината е откачена, RADIATIVE")

    # Засилване на вятъра → ще разбие инверсията
    if dV_wind > 5.0:
        return "dynamic", 3600, f"ΔV={dV_wind:.1f}m/s > 5m/s → засилване на вятъра"

    # ── Крайбрежно с умерен вятър → ADVECTIVE τ=1h ──
    if cfg.get("coastal") and V_sfc > 3.0:
        return "advective", 3600, f"Крайбрежно, V={V_sfc:.1f}m/s → морска адвекция"

    # ── Нищо → RADIATIVE ──
    return "radiative", None, "Стационарна ситуация → радиационен случай, без nudging"

# ──────────────────────────────────────────────────────────────
# Nudging
# ──────────────────────────────────────────────────────────────

def apply_nudging(model, icon_prof, tau_T, tau_qv):
    """Двуканален nudging: T бързо, qv по-бавно."""
    # Защита: icon_prof може да има различен брой нива от model.z
    z_src = np.asarray(icon_prof["z"], dtype=float)
    if len(z_src) < 2:
        return   # недостатъчно нива за интерполация
    # Ограничаваме model.z до диапазона на icon_prof["z"]
    z_clip = np.clip(model.z, z_src[0], z_src[-1])
    T_i  = np.interp(z_clip, z_src, icon_prof["T"])
    qv_i = np.interp(z_clip, z_src, icon_prof["qv"])
    u_i  = np.interp(z_clip, z_src, icon_prof["u"])
    v_i  = np.interp(z_clip, z_src, icon_prof["v"])

    aT  = model.dt / tau_T
    aqv = model.dt / tau_qv

    model.T  += aT  * (T_i  - model.T)
    model.u  += aT  * (u_i  - model.u)
    model.v  += aT  * (v_i  - model.v)
    model.qv += aqv * (qv_i - model.qv)
    model.qv  = np.maximum(model.qv, 1e-8)
    # ql никога не се nudge-ва



# ──────────────────────────────────────────────────────────────
# Приземна параметризация (COBEL-ABLE стил)
# ──────────────────────────────────────────────────────────────

def build_surface_layer(profile: dict, metar: dict, doy: int) -> dict:
    """
    Конструира реалистичен приземен слой (0-300m AGL) от METAR
    и го залепя към ICON профила отгоре.

    Физика:
    - T при z=2m = METAR T
    - Td при z=2m = METAR Td → qv приземно
    - Температурен профил в инверсионния слой (0-150m):
        при стабилна нощ: +0.5 K/100m (слаба инверсия)
        при неутрален:     -0.65 K/100m (сух адиабат)
    - Преход към ICON профила между 150-300m AGL
    - Долните нива на ICON се заменят, горните остават

    Параметри
    ----------
    profile : dict от fetch_icon_historical
    metar   : dict от parse_metar
    doy     : ден от годината

    Връща обновен profile dict
    """
    import numpy as np
    from fog_model import sat_vapor_pressure, eps_r, Rd, g

    if metar.get("T") is None:
        return profile   # без METAR данни — оставяме ICON

    T_sfc  = metar["T"]  + 273.15   # K
    Td_sfc = (metar["Td"] + 273.15) if metar.get("Td") is not None else T_sfc - 3.0

    # Нощен час → инверсия; дневен → неутрален
    hour = metar.get("hour", 0) or 0
    is_night = (hour >= 18 or hour <= 8)

    # Вертикален температурен градиент в приземния слой
    # Приоритет: изчисляваме от METAR (2m) и първото ICON ниво
    # Това улавя реалната инверсия вместо да предполагаме фиксирана
    z_icon_0 = float(profile["z"][0]) if len(profile["z"]) > 0 else 200.0
    T_icon_0 = float(profile["T"][0]) if len(profile["T"]) > 0 else T_sfc + 2.0

    if z_icon_0 > 10.0:
        # Реален градиент от METAR до първото ICON ниво
        dT_dz_real = (T_icon_0 - T_sfc) / z_icon_0   # K/m
        # Физически граници: -10 K/km (суперадиабат) до +30 K/km (силна инверсия)
        dT_dz = float(np.clip(dT_dz_real, -10.0/1000.0, 30.0/1000.0))
        source = f"METAR→ICON ({z_icon_0:.0f}m): {dT_dz*1000:.1f} K/km"
    else:
        # Fallback при липса на ICON нива
        if is_night:
            dT_dz = +3.0 / 1000.0
        else:
            dT_dz = -6.5 / 1000.0
        source = "фиксиран fallback"

    # Строим нива 2-300m с 10m стъпка
    z_sfc   = np.arange(2, 302, 10, dtype=float)   # 30 нива
    T_sfc_v = T_sfc + dT_dz * z_sfc                # линеен профил в долния слой
    print(f"[SFC LAYER] dT/dz={dT_dz*1000:+.1f} K/km  ({source})")

    # qv от Td (приземно), намалява с височина
    es_sfc  = sat_vapor_pressure(Td_sfc)
    p_sfc   = profile["p"][0] if len(profile["p"]) > 0 else 95000.0
    qv_sfc  = eps_r * es_sfc / (p_sfc - es_sfc)
    # qv намалява бавно с exp скала ~800m (почти постоянна в PBL)
    qv_sfc_v = qv_sfc * np.exp(-z_sfc / 800.0) + 1e-8

    # Налягане по хидростатика
    rho_sfc = p_sfc / (Rd * T_sfc)
    p_sfc_v = p_sfc * np.exp(-g * z_sfc / (Rd * T_sfc))

    # Вятър: интерполираме от METAR приземен вятър към ICON
    if metar.get("wind_speed") is not None:
        ws_ms = metar["wind_speed"] * 0.5144
        wd    = metar.get("wind_dir") or 0
        u_sfc = -ws_ms * np.sin(np.deg2rad(wd))
        v_sfc = -ws_ms * np.cos(np.deg2rad(wd))
    else:
        u_sfc = profile["u"][0]
        v_sfc = profile["v"][0]

    # Вятърът нараства логаритмично с z (log-law)
    z0 = 0.1   # аеродинамична грапавост [m]
    log_fac = np.log(np.maximum(z_sfc, 1.0) / z0) / np.log(10.0 / z0)
    log_fac = np.clip(log_fac, 0.0, 1.5)
    u_sfc_v  = u_sfc * log_fac
    v_sfc_v  = v_sfc * log_fac

    # ── Залепване към ICON профила ──
    # Намираме индекса в ICON където z > 300m
    z_icon = profile["z"]
    idx_join = np.searchsorted(z_icon, 300.0)
    idx_join = max(idx_join, 1)   # поне 1 ниво от ICON

    # Преходна зона: blending между 150-300m
    z_icon_upper = z_icon[idx_join:]
    T_icon_upper = profile["T"][idx_join:]
    qv_icon_upper = profile["qv"][idx_join:]
    p_icon_upper  = profile["p"][idx_join:]
    u_icon_upper  = profile["u"][idx_join:]
    v_icon_upper  = profile["v"][idx_join:]

    # Обединен профил
    z_new  = np.concatenate([z_sfc,       z_icon_upper])
    T_new  = np.concatenate([T_sfc_v,     T_icon_upper])
    qv_new = np.concatenate([qv_sfc_v,    qv_icon_upper])
    p_new  = np.concatenate([p_sfc_v,     p_icon_upper])
    u_new  = np.concatenate([u_sfc_v,     u_icon_upper])
    v_new  = np.concatenate([v_sfc_v,     v_icon_upper])

    # Сортиране по z и премахване на дубликати
    idx = np.argsort(z_new)
    z_new  = z_new[idx];  T_new  = T_new[idx]
    qv_new = qv_new[idx]; p_new  = p_new[idx]
    u_new  = u_new[idx];  v_new  = v_new[idx]

    # Премахваме дублирани z стойности (могат да се появят при залепването)
    _, unique_idx = np.unique(z_new, return_index=True)
    z_new  = z_new[unique_idx];  T_new  = T_new[unique_idx]
    qv_new = qv_new[unique_idx]; p_new  = p_new[unique_idx]
    u_new  = u_new[unique_idx];  v_new  = v_new[unique_idx]

    print(f"[SFC LAYER] Построен от METAR: T={T_sfc-273.15:.1f}°C  "
          f"Td={Td_sfc-273.15:.1f}°C  {'инверсия' if is_night else 'неутрален'}")
    print(f"[SFC LAYER] {len(z_new)} нива  "
          f"z=[{z_new[0]:.0f}–{z_new[-1]:.0f}m]  "
          f"T[0]={T_new[0]-273.15:.1f}°C  T[5]={T_new[5]-273.15:.1f}°C")

    prof = dict(profile)
    prof["z"]  = z_new
    prof["T"]  = T_new
    prof["qv"] = np.maximum(qv_new, 1e-8)
    prof["p"]  = p_new
    prof["u"]  = u_new
    prof["v"]  = v_new
    return prof

# ──────────────────────────────────────────────────────────────
# Основна функция
# ──────────────────────────────────────────────────────────────

def run_case(icao: str, date_str: str, hour0: int,
             metar_raw: str | None, hours: float = 12.0,
             dt: float = 60.0, use_nudging: bool = True,
             out_dir: str = "case_output") -> list:

    cfg = AIRPORT_CONFIG[icao]
    print(f"\n{'═'*60}")
    print(f"  {icao}  {AIRPORT_COORDS[icao]['name']}  |  {date_str} {hour0:02d} UTC")
    print(f"{'═'*60}")

    # 1. ICON исторически профил
    profile = fetch_icon_historical(icao, date_str, hour0,
                                    forecast_hours=int(hours)+1)

    # 2. METAR корекция
    metar_dict = {}
    if metar_raw:
        metar_dict = parse_metar(metar_raw)
        profile    = apply_metar_correction(profile, metar_dict)
        print(f"[METAR] {metar_raw[:60]}...")
    else:
        profile.setdefault("ql_init", np.zeros_like(profile["T"]))
        print("[METAR] Няма наблюдение — без корекция")

    # 3. Ден от годината (нужен за радиация и приземен слой)
    from datetime import datetime
    doy = datetime.strptime(date_str, "%Y-%m-%d").timetuple().tm_yday

    # 4. Приземна параметризация — COBEL-ABLE стил
    # Строим долния слой от METAR, залепяме към ICON горния
    profile = build_surface_layer(profile, metar_dict, doy)

    # 5. Инициализация на модела
    # Р2: Логаритмична вертикална мрежа (Tardif 2007)
    # Фина при земята (Δz~0.14m @ z=0.5m), груба горе (Δz~100m @ z=2000m)
    # Критично за приземното охлаждане и образуването на мъгла
    z_log   = np.logspace(np.log10(0.5), np.log10(50), 20)
    z_lin   = np.linspace(55, 2000, 20)
    z_model = np.concatenate([z_log, z_lin])
    T_m  = np.interp(z_model, profile["z"], profile["T"])
    qv_m = np.interp(z_model, profile["z"], profile["qv"])
    p_m  = np.interp(z_model, profile["z"], profile["p"])
    u_m  = np.interp(z_model, profile["z"], profile["u"])
    v_m  = np.interp(z_model, profile["z"], profile["v"])

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=float(hour0), dt=dt, day_of_year=doy)

    # Инициализираме почвените температури от ICON
    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        model.T_skin = float(T_soil_icon)   # старт: повърхността ≈ почвата
        model.T_skin = min(model.T_skin, model.T[0])  # не по-топла от въздуха
        model._log_qv = True   # qv профил лог
        print(f"[SOIL] T_soil от ICON: {T_soil_icon-273.15:.1f}°C  "
              f"(T_air={T_m[0]-273.15:.1f}°C  ΔT={T_m[0]-T_soil_icon:+.1f}K)")
    else:
        print(f"[SOIL] T_soil не е налична — използваме T_air")
    # ql_init може да има различна дължина от profile["z"] след build_surface_layer
    # Затова го нулираме и прилагаме само ако има мъгла в METAR
    ql_init_raw = profile.get("ql_init", None)
    if ql_init_raw is not None and len(ql_init_raw) == len(profile["z"]):
        model.ql = np.interp(z_model, profile["z"], ql_init_raw)
    elif ql_init_raw is not None and np.any(np.array(ql_init_raw) > 0):
        # Мъгла в METAR — инициализираме с малко LWC в долните 50m
        model.ql = np.where(z_model < 50, np.max(ql_init_raw), 0.0)
    else:
        model.ql = np.zeros(len(z_model))

    # 5. Диагностика на режима
    regime, tau, reason = diagnose_regime(profile, metar_dict, cfg)
    print(f"[РЕЖИМ] {regime.upper()}  —  {reason}")
    if tau:
        print(f"[РЕЖИМ] τ_relax = {tau//3600}h")
    else:
        print(f"[РЕЖИМ] Без nudging — 1D физика управлява")

    # 6. Интеграция
    steps_total  = int(hours * 3600 / dt)
    steps_per_hr = int(3600 / dt)
    hourly_profs = profile.get("hourly_profiles", [])

    model.diagnose()
    r0 = model.history[-1]
    print(f"\n{'Час UTC':>8} | {'T°C':>6} | {'RH%':>5} | {'LWC g/m³':>9} | {'VIS m':>7} | CAT")
    print("-"*55)
    print(f"{r0['hour_utc']:8.1f} | {r0['T_sfc']-273.15:6.1f} | "
          f"{r0['rh_sfc']*100:5.1f} | {r0['ql_sfc']*1000:9.4f} | "
          f"{r0['vis_sfc']:7.0f} | {r0['cat']}")

    # Текущ режим и tau — ще се преоценяват hourly
    current_regime = regime
    current_tau    = tau
    pending_regime = None   # кандидат за смяна (трябва 2 последователни часа)
    pending_count  = 0      # брой последователни часа с новия режим

    from fog_model import _sin_elevation
    import io, sys as _sys

    for step in range(1, steps_total + 1):
        model.step()

        hour_elapsed = step * dt / 3600.0
        prof_idx     = min(int(hour_elapsed), len(hourly_profs) - 1)

        # ── Hourly reassessment на режима ──────────────────────────────
        if step % steps_per_hr == 0 and hourly_profs:
            hour_now  = (float(hour0) + hour_elapsed) % 24
            hour_next = (hour_now + 1) % 24
            sin_el      = _sin_elevation(hour_now,  doy)
            sin_el_next = _sin_elevation(hour_next, doy)
            is_sunrise  = sin_el > 0.05 and sin_el_next > sin_el
            is_sunset   = sin_el > 0.05 and sin_el_next < sin_el

            # Диагностика от ICON профилите напред
            remaining_profs = hourly_profs[prof_idx:]
            if len(remaining_profs) < 3:
                remaining_profs = hourly_profs[-3:]
            _old_stdout = _sys.stdout
            _sys.stdout = io.StringIO()
            cand_regime, cand_tau, cand_reason = diagnose_regime(
                {"hourly_profiles": remaining_profs}, metar_dict, cfg)
            _sys.stdout = _old_stdout

            # ── Специални правила за изгрев/залез ──
            # Изгрев → RADIATIVE → DYNAMIC (веднага, без изчакване)
            if is_sunrise and current_regime == "radiative":
                cand_regime = "dynamic"
                cand_tau    = 7200
                cand_reason = f"Изгрев (sin_el={sin_el:.2f}↑) → nudging T τ=2h"

            # При изгрев не се връщаме към RADIATIVE
            if current_regime == "dynamic" and cand_regime == "radiative" and is_sunrise:
                cand_regime = "dynamic"
                cand_tau    = current_tau
                cand_reason = "Изгрев продължава — задържаме DYNAMIC"

            # Залез + тихо → DYNAMIC → RADIATIVE позволено
            # (при залез вятърът е отслабнал и радиационното охлаждане доминира)
            if current_regime == "dynamic" and cand_regime == "radiative" and is_sunset:
                pass   # разрешаваме смяната — продължава към pending логиката

            # ── Правило за 2 последователни часа ──
            if cand_regime != current_regime:
                if cand_regime == pending_regime:
                    pending_count += 1
                else:
                    pending_regime = cand_regime
                    pending_count  = 1

                # Смяна при 2 последователни часа (или веднага при изгрев)
                threshold = 1 if is_sunrise else 2
                if pending_count >= threshold:
                    print(f"  [РЕЖИМ →] {hour_now:.0f}UTC: "
                          f"{current_regime.upper()} → {cand_regime.upper()} | {cand_reason}")
                    current_regime = cand_regime
                    current_tau    = cand_tau
                    pending_regime = None
                    pending_count  = 0
            else:
                # Режимът потвърден — нулираме pending
                pending_regime = None
                pending_count  = 0

        # ── Nudging с текущия режим ────────────────────────────────────
        if use_nudging and current_tau and hourly_profs:
            apply_nudging(model, hourly_profs[prof_idx],
                          cfg["tau_T"], current_tau)

        # SST ограничение само за морска адвекция
        if cfg.get("coastal") and current_regime == "advective":
            sst = get_sst(date_str)
            T_floor = sst - 2.0 + 273.15
            model.T = np.maximum(model.T, T_floor)

        # Изход на всеки час
        if step % steps_per_hr == 0:
            r = model.diagnose()
            print(f"{r['hour_utc']:8.1f} | {r['T_sfc']-273.15:6.1f} | "
                  f"{r['rh_sfc']*100:5.1f} | {r['ql_sfc']*1000:9.4f} | "
                  f"{r['vis_sfc']:7.0f} | {r['cat']}")


    # 5. Изход
    os.makedirs(out_dir, exist_ok=True)
    label = f"{icao}_{date_str}_{hour0:02d}UTC"
    save_surface_csv(model.history,
                     out_path=os.path.join(out_dir, f"{label}_forecast.csv"))
    plot_forecast(model.history,
                  out_png=os.path.join(out_dir, f"{label}_forecast.png"))
    print_summary_report(model.history, station=icao)

    return model.history


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ретроспективен анализ на мъглен случай — ICON-EU + METAR")
    parser.add_argument("--date",     required=True,
                        help="Дата на инициализацията YYYY-MM-DD")
    parser.add_argument("--hour",     type=int, required=True,
                        help="UTC час на инициализацията (0-23)")
    parser.add_argument("--airports", nargs="*",
                        default=["LBSF", "LBWN", "LBBG", "LBPD", "LBGO"])
    parser.add_argument("--hours",    type=float, default=12.0)
    parser.add_argument("--dt",       type=float, default=60.0)
    parser.add_argument("--no-nudge",     action="store_true")
    parser.add_argument("--metar-source", type=str, default="awc",
                        choices=["awc","iem","ogimet"],
                        help="awc=aviationweather.gov  iem=Iowa State  ogimet=OGIMET (препоръчан за архив)")
    parser.add_argument("--verify",       action="store_true",
                        help="Верификация с реални METAR след прогнозата")
    parser.add_argument("--out",      type=str, default="case_output")
    args = parser.parse_args()

    airports = [a.upper() for a in args.airports]
    valid = ["LBSF", "LBWN", "LBBG", "LBPD", "LBGO"]
    airports = [a for a in airports if a in valid]
    if not airports:
        print(f"[ГРЕШКА] Невалидни ICAO кодове. Налични: {', '.join(valid)}")
        sys.exit(1)

    print(f"\n{'═'*60}")
    print(f"  РЕТРОСПЕКТИВЕН АНАЛИЗ — {args.date} {args.hour:02d} UTC")
    print(f"  Летища: {', '.join(airports)}")
    print(f"  Прогноза: +{args.hours:.0f}h")
    print(f"{'═'*60}")

    # Изтегли METAR — избор на източник
    if args.metar_source == "iem":
        from iem_fetcher import fetch_all_iem
        from datetime import datetime as _dt, timedelta as _td
        print(f"[METAR] Източник: Iowa State IEM архив")
        dt_next = (_dt.strptime(args.date, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
        obs1 = fetch_all_iem(airports, args.date)
        obs2 = fetch_all_iem(airports, dt_next)
        all_metars = {}
        _raw = {}
        for icao in airports:
            combined = obs1.get(icao,[]) + obs2.get(icao,[])
            _raw[icao] = combined
            all_metars[icao] = [{"time": o["time"], "raw": o.get("raw","")} for o in combined]
        all_metars["_raw"] = _raw
    elif args.metar_source == "ogimet":
        from ogimet_fetcher import fetch_all_ogimet
        print(f"[METAR] Източник: OGIMET архив")
        obs_ogimet = fetch_all_ogimet(airports, args.date,
                                       hour0=args.hour, hours=int(args.hours)+4)
        all_metars = {}
        for icao in airports:
            all_metars[icao] = [{"time": o["time"], "raw": o.get("raw","")}
                                  for o in obs_ogimet.get(icao,[])]
        all_metars["_raw"] = obs_ogimet
    else:
        print(f"[METAR] Източник: aviationweather.gov")
        hours_back = int(args.hours) + 4
        all_metars = fetch_metar_historical(airports, hours_back=hours_back)
        all_metars["_raw"] = {}

    # Пусни модела за всяко летище
    all_results = {}
    for icao in airports:
        metar_raw, metar_h, metar_mn = find_metar_at(all_metars, icao, args.hour, args.date)
        # Стартираме от часа на METAR, не от заявения час
        actual_hour = metar_h if metar_raw else args.hour
        if metar_raw:
            print(f"\n[{icao}] METAR @ {metar_h:02d}:{metar_mn:02d}UTC: {metar_raw}")
            if actual_hour != args.hour:
                print(f"[{icao}] Стартираме от {actual_hour:02d}:00 UTC (METAR час)")
        else:
            print(f"\n[{icao}] Няма METAR за {args.hour:02d}UTC")

        try:
            history = run_case(
                icao       = icao,
                date_str   = args.date,
                hour0      = actual_hour,
                metar_raw  = metar_raw,
                hours      = args.hours,
                dt         = args.dt,
                use_nudging= not args.no_nudge,
                out_dir    = args.out,
            )
            all_results[icao] = history
        except Exception as e:
            print(f"\n[ГРЕШКА] {icao}: {e}")
            all_results[icao] = []

    # Обобщена таблица
    SYM = {"LIFR":"█","IFR":"▓","MVFR":"░","VFR":"·"}
    print(f"\n\n{'═'*68}")
    print(f"  ОБОБЩЕНИЕ — {args.date} {args.hour:02d} UTC → +{args.hours:.0f}h  (старт от METAR час)")
    print(f"{'═'*68}")
    print(f"  {'ICAO':<6} {'Летище':<16} {'Мин.VIS':>8} {'@UTC':>5} {'<1000m':>6}   Оценка")
    print(f"  {'-'*64}")

    for icao in airports:
        history = all_results.get(icao, [])
        if not history:
            print(f"  {icao:<6} {'—':16}  {'ГРЕШКА':>8}")
            continue
        name    = AIRPORT_COORDS[icao]["name"]
        min_vis = min(r["vis_sfc"] for r in history)
        min_t   = next(r["hour_utc"] for r in history if r["vis_sfc"] == min_vis)
        fog_h   = sum(1 for r in history if r["vis_sfc"] < 1000)
        if   min_vis < 200:  rating = "LIFR  ⛔ Силна мъгла"
        elif min_vis < 600:  rating = "IFR   ⚠ Мъгла"
        elif min_vis < 1000: rating = "MVFR  ~ Намалена VIS"
        else:                rating = "VFR   ✓ Ясно"
        print(f"  {icao:<6} {name:<16} {min_vis:>7.0f}m "
              f"{min_t:>5.0f} {fog_h:>4}h   {rating}")

    print(f"{'═'*68}")
    print(f"\n  Timeline  (█LIFR  ▓IFR  ░MVFR  ·VFR)")
    for icao in airports:
        history = all_results.get(icao, [])
        if history:
            bar = " ".join(SYM[r["cat"]] for r in history)
            print(f"  {icao:<5}  {bar}")

    # Верификация
    if args.verify:
        if args.metar_source == "ogimet":
            from ogimet_fetcher import verify_forecast
        else:
            from iem_fetcher import verify_forecast
        raw_obs = all_metars.get("_raw", {})
        for icao in airports:
            hist = all_results.get(icao, [])
            if hist and raw_obs.get(icao):
                h0 = int(hist[0]["hour_utc"])
                try:
                    verify_forecast(hist, icao, args.date, h0,
                                    {icao: raw_obs[icao]})
                except Exception as e:
                    print(f"[ВЕРИФИКАЦИЯ] {icao}: {e}")

    print(f"\n[ИЗХОД] Файлове в: {args.out}/")


if __name__ == "__main__":
    main()
