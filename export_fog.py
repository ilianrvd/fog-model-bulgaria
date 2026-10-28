"""
export_fog.py
=============
Машинен изход на оперативния пробег: docs/data/fog.json.

Чете се от отделния проект airport-nowcast, където моделът за мъгла е
КОРЕКТОР на видимостта от NWP. Затова тук са суровите почасови стойности
и признаците за известните слаби места — без допълнителна обработка.

Правила:
  - Нула физика. Само четене от model.history, от облачната серия на ICON,
    която моделът вече е получил (model.cc_series), и от началния METAR.
  - Нула нови сваляния.
  - Вика се в края на run_operational.main() в try/except: грешка тук
    не може да спре прогнозата или страницата.

Дефинициите на дебелината на мъглата и на LWP са ДОСЛОВНО тези от
verify_cases.py (Фаза А), за да са сравними с верификационния набор.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import numpy as np

from fog_model import sat_vapor_pressure, eps_r
from verify_cases import _fog_top_m, _lwp_kg_m2

OUT_PATH = os.path.join("docs", "data", "fog.json")

FOG_VIS_M       = 1000.0   # „мъгла" — същият праг като forecast_fog на страницата
VIS2_M          = 2000.0
LAST_VALID_HOUR = 7        # часове от init до 07 UTC на следващата сутрин
PRECIP_MM_H     = 0.1      # същият праг, при който моделът приема облака за реален
Z_2M            = 2.0


# ──────────────────────────────────────────────────────────────
# Помощни
# ──────────────────────────────────────────────────────────────

def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None


def _rnd(x, n):
    return None if x is None else round(float(x), n)


def _td_from_qv(qv, p):
    """Точка на оросяване [°C] от qv [kg/kg] и p [Pa] — обратната на
    формулата на Buck от fog_model.sat_vapor_pressure."""
    e = qv * p / (eps_r + qv * (1.0 - eps_r))
    if e <= 0:
        return None
    a = np.log(e / 611.2)
    return 243.5 * a / (17.67 - a)


def _rh_from_t_td(t, td):
    if t is None or td is None:
        return None
    es_t  = sat_vapor_pressure(np.array([t + 273.15]))[0]
    es_td = sat_vapor_pressure(np.array([td + 273.15]))[0]
    return float(es_td / es_t)


def _cf_combined(row):
    """Сурова комбинирана облачност от (lo, mid, hi, ...) — ДОСЛОВНО
    формулата от run_case.fetch_icon_historical / fog_model.step,
    БЕЗ дисконта ×0.2 за ниската облачност при моделна RH>95%."""
    lo, mi, hi = float(row[0]), float(row[1]), float(row[2])
    return 1.0 - (1.0 - lo) * (1.0 - 0.7 * mi) * (1.0 - 0.25 * hi)


def model_version():
    try:
        out = subprocess.run(["git", "describe", "--tags", "--always"],
                             capture_output=True, text=True, timeout=10)
        v = out.stdout.strip()
        if v:
            return v
    except Exception:
        pass
    try:
        with open("baseline.json", encoding="utf-8") as f:
            return json.load(f).get("tag")
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# Едно летище
# ──────────────────────────────────────────────────────────────

def build_airport(icao, run, metar_raw, metar_dict):
    """run — речникът от run_operational.run_airport.last_run."""
    model   = run["model"]
    history = model.history
    init    = run["init"]
    regimes = run.get("regime_hourly") or []
    ccs     = getattr(model, "cc_series", None) or []
    p_col   = model.p
    p0      = float(model.p[0])

    last_valid = (init.replace(hour=0) + timedelta(days=1)
                  + timedelta(hours=LAST_VALID_HOUR))

    hours = []
    for k, h in enumerate(history):
        valid = init + timedelta(hours=float(h["time_h"]))
        if valid > last_valid:
            break
        z  = np.asarray(h["z"], dtype=float)
        T  = np.asarray(h["T"], dtype=float)
        qv = np.asarray(h["qv"], dtype=float)
        ql = np.asarray(h["ql"], dtype=float)

        t2  = float(np.interp(Z_2M, z, T)) - 273.15
        q2  = float(np.interp(Z_2M, z, qv))
        p2  = float(np.interp(Z_2M, z, p_col))
        td2 = _td_from_qv(q2, p2)

        ci  = min(int(float(h["time_h"])), len(ccs) - 1) if ccs else None
        row = ccs[ci] if ci is not None else None

        reg = regimes[k] if k < len(regimes) else None
        hours.append({
            "valid"      : _iso(valid),
            "vis_m"      : int(round(float(h["vis_sfc"]))),
            "lwp"        : _rnd(_lwp_kg_m2(z, p_col, T, ql), 8),
            "fog_depth_m": _rnd(_fog_top_m(z, ql), 2),
            "regime"     : reg.upper() if reg else None,
            "t2m"        : _rnd(t2, 1),
            "td2m"       : _rnd(td2, 1),
            "cf"         : _rnd(_cf_combined(row), 2) if row else None,
            "cf_lo"      : _rnd(row[0], 2) if row else None,
            "cf_mid"     : _rnd(row[1], 2) if row else None,
            "cf_hi"      : _rnd(row[2], 2) if row else None,
        })

    # ── Резюме ──
    vis   = [e["vis_m"] for e in hours]
    fogix = [i for i, v in enumerate(vis) if v < FOG_VIS_M]
    summary = {
        "fog"         : bool(fogix),
        "onset"       : hours[fogix[0]]["valid"] if fogix else None,
        "end"         : hours[fogix[-1]]["valid"] if fogix else None,
        "min_vis_m"   : min(vis) if vis else None,
        "hours_lt1000": len(fogix),
        "hours_lt2000": sum(1 for v in vis if v < VIS2_M),
    }

    # ── Признаци ──
    cfs = [e["cf"] for e in hours if e["cf"] is not None]
    cf_collapse = None
    cf_high     = None
    if cfs:
        cf_collapse = any(
            (cfs[i] - cfs[j]) > 0.5
            for i in range(len(cfs))
            for j in (i + 1, i + 2) if j < len(cfs))
        cf_high = sum(1 for c in cfs if c > 0.9) > len(cfs) / 2.0

    precip = None
    if ccs:
        n = len(hours)
        prs = [float(r[4]) for r in ccs[:n] if len(r) > 4]
        precip = any(p >= PRECIP_MM_H for p in prs) if prs else None

    mv = summary["min_vis_m"]
    borderline = (900 <= mv <= 1100) if mv is not None else None

    t_m  = metar_dict.get("T")
    td_m = metar_dict.get("Td")
    saturated_start = (abs(t_m - td_m) <= 0.5) \
        if (t_m is not None and td_m is not None) else None

    river = None
    if icao == "LBGO":
        ws = metar_dict.get("wind_speed")
        rh = _rh_from_t_td(t_m, td_m)
        if ws is not None and rh is not None:
            river = (ws < 4) and (rh > 0.95)

    return {
        "init_metar": metar_raw or None,
        "hours"     : hours,
        "summary"   : summary,
        "flags"     : {
            "cf_collapse"    : cf_collapse,
            "cf_high"        : cf_high,
            "borderline"     : borderline,
            "saturated_start": saturated_start,
            "precip"         : precip,
            "river_regime"   : river,
        },
    }


# ──────────────────────────────────────────────────────────────
# Целият файл
# ──────────────────────────────────────────────────────────────

def build_payload(runs, metars, metar_dicts, errors, created=None):
    """
    runs        — {icao: run_airport.last_run}
    metars      — {icao: суров METAR}
    metar_dicts — {icao: parse_metar(...)}
    errors      — {icao: текст на грешката} за неуспешните летища
    """
    created = created or datetime.now(timezone.utc)
    inits = [r["init"] for r in runs.values() if r.get("init")]
    payload = {
        "created"      : _iso(created),
        "init"         : _iso(min(inits)) if inits else None,
        # historical-forecast-api на Open-Meteo сглобява серията от
        # последните налични пускания и не връща кой run е ползван.
        "icon_run"     : None,
        "model_version": model_version(),
        "airports"     : {},
    }
    for icao in list(runs) + [i for i in errors if i not in runs]:
        if icao in runs:
            try:
                payload["airports"][icao] = build_airport(
                    icao, runs[icao], metars.get(icao),
                    metar_dicts.get(icao) or {})
            except Exception as e:
                payload["airports"][icao] = {"init_metar": metars.get(icao),
                                             "error": f"export: {e}"}
        else:
            payload["airports"][icao] = {"init_metar": metars.get(icao),
                                         "error": errors[icao]}
    return payload


def write_payload(payload, path=OUT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path
