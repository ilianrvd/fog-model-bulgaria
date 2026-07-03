"""
run_operational.py
==================
Оперативен скрипт за GitHub Actions.
Пуска 1D модела за 5-те летища с текущи данни (ICON-EU + METAR).
Записва резултатите в results/latest.json и docs/index.html.

Пуска се автоматично от GitHub Actions всеки час 18-23 UTC.
"""

import sys, os, json, numpy as np
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from fog_model      import FogModel1D
from icon_reader    import fetch_icon_eu, AIRPORT_COORDS
from metar_fetcher  import fetch_all_airports
from metar_parser   import parse_metar, apply_metar_correction
from run_case       import build_surface_layer, diagnose_regime, apply_nudging, AIRPORT_CONFIG, get_sst

ALL_AIRPORTS = ["LBSF", "LBWN", "LBBG", "LBPD", "LBGO"]

CAT_COLOR = {
    "LIFR": "#ff4444",
    "IFR":  "#ff8800",
    "MVFR": "#ffcc00",
    "VFR":  "#44cc44",
}
CAT_BG = {
    "LIFR": "#3a0000",
    "IFR":  "#3a1a00",
    "MVFR": "#2a2a00",
    "VFR":  "#003a00",
}

def run_airport(icao, metar_raw, hours=12, dt=60):
    cfg = AIRPORT_CONFIG[icao]
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    doy = now.timetuple().tm_yday

    profile = fetch_icon_eu(icao, forecast_hours=int(hours)+1)

    metar_dict = {}
    if metar_raw:
        metar_dict = parse_metar(metar_raw)
        profile    = apply_metar_correction(profile, metar_dict)
    else:
        profile.setdefault("ql_init", np.zeros_like(profile["T"]))

    profile = build_surface_layer(profile, metar_dict, doy)

    regime, tau, reason = diagnose_regime(profile, metar_dict, cfg)

    z_model = np.linspace(2., 2000., 40)
    T_m  = np.interp(z_model, profile["z"], profile["T"])
    qv_m = np.interp(z_model, profile["z"], profile["qv"])
    p_m  = np.interp(z_model, profile["z"], profile["p"])
    u_m  = np.interp(z_model, profile["z"], profile["u"])
    v_m  = np.interp(z_model, profile["z"], profile["v"])

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=profile["hour0"], dt=dt, day_of_year=doy)

    ql_init_raw = profile.get("ql_init", None)
    if ql_init_raw is not None and len(ql_init_raw) == len(profile["z"]):
        model.ql = np.interp(z_model, profile["z"], ql_init_raw)
    else:
        model.ql = np.zeros(len(z_model))

    steps_total  = int(hours * 3600 / dt)
    steps_per_hr = int(3600 / dt)
    hourly_profs = profile.get("hourly_profiles", [])

    model.diagnose()

    current_regime = regime
    current_tau    = tau

    for step in range(1, steps_total + 1):
        model.step()

        hour_elapsed = step * dt / 3600.0
        prof_idx     = min(int(hour_elapsed), len(hourly_profs)-1)

        # Hourly reassessment
        if step % steps_per_hr == 0 and hourly_profs:
            remaining = hourly_profs[prof_idx:]
            if len(remaining) < 3:
                remaining = hourly_profs[-3:]
            import io, sys as _sys
            _old_stdout = _sys.stdout
            _sys.stdout = io.StringIO()
            new_regime, new_tau, _ = diagnose_regime(
                {"hourly_profiles": remaining}, {}, cfg)
            _sys.stdout = _old_stdout

            from fog_model import _sin_elevation
            hour_now = (float(profile["hour0"]) + hour_elapsed) % 24
            sin_el = _sin_elevation(hour_now, doy)

            if sin_el > 0.1 and current_regime == "radiative":
                new_regime = "dynamic"
                new_tau    = 7200

            # След изгрев не се връщаме към RADIATIVE
            if current_regime == "dynamic" and new_regime == "radiative":
                if sin_el > 0.05:
                    new_regime = "dynamic"
                    new_tau    = current_tau

            current_regime = new_regime
            current_tau    = new_tau

        if current_tau and hourly_profs:
            apply_nudging(model, hourly_profs[prof_idx], cfg["tau_T"], current_tau)

        if step % steps_per_hr == 0:
            model.diagnose()

    return model.history, regime, reason


def main():
    now_utc = datetime.now(timezone.utc)
    run_time = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n[FOG NOWCAST] {run_time}")

    # METAR
    try:
        metars = fetch_all_airports(ALL_AIRPORTS)
    except Exception as e:
        print(f"METAR грешка: {e}")
        metars = {}

    # Модел за всяко летище
    results = {}
    for icao in ALL_AIRPORTS:
        print(f"\n--- {icao} ---")
        try:
            history, regime, reason = run_airport(icao, metars.get(icao))
            min_vis = min(r["vis_sfc"] for r in history)
            min_t   = next(r["hour_utc"] for r in history if r["vis_sfc"] == min_vis)
            fog_h   = sum(1 for r in history if r["vis_sfc"] < 1000)
            cats    = [r["cat"] for r in history]
            hours_utc = [r["hour_utc"] for r in history]
            vis_list  = [r["vis_sfc"] for r in history]
            T_list    = [round(r["T_sfc"] - 273.15, 1) for r in history]

            if   min_vis < 200:  rating = "LIFR"
            elif min_vis < 600:  rating = "IFR"
            elif min_vis < 1000: rating = "MVFR"
            else:                rating = "VFR"

            results[icao] = {
                "name"     : AIRPORT_COORDS[icao]["name"],
                "min_vis"  : int(min_vis),
                "min_t_utc": float(min_t),
                "fog_hours": fog_h,
                "rating"   : rating,
                "regime"   : regime,
                "cats"     : cats,
                "hours_utc": hours_utc,
                "vis"      : vis_list,
                "T"        : T_list,
            }
            print(f"  Мин.VIS={min_vis:.0f}m  {rating}  Режим={regime}")
        except Exception as e:
            print(f"  ГРЕШКА: {e}")
            results[icao] = {"name": AIRPORT_COORDS[icao]["name"], "error": str(e)}

    # Запис на JSON
    os.makedirs("results", exist_ok=True)
    payload = {"run_time": run_time, "airports": results}
    with open("results/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("\n[OK] results/latest.json записан")

    # Генериране на HTML
    os.makedirs("docs", exist_ok=True)
    html = build_html(payload)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[OK] docs/index.html записан")


def build_html(payload):
    run_time = payload["run_time"]
    airports = payload["airports"]

    SYM = {"LIFR": "█", "IFR": "▓", "MVFR": "░", "VFR": "·"}

    rows = ""
    for icao, d in airports.items():
        if "error" in d:
            rows += f"""
            <tr>
              <td><b>{icao}</b></td>
              <td>{d['name']}</td>
              <td colspan="5" style="color:#ff4444">ГРЕШКА: {d['error']}</td>
            </tr>"""
            continue

        rating  = d["rating"]
        color   = CAT_COLOR.get(rating, "#ffffff")
        bg      = CAT_BG.get(rating, "#111")
        min_vis = d["min_vis"]
        fog_h   = d["fog_hours"]
        regime  = d["regime"].upper()

        # Timeline символи
        timeline = ""
        for cat in d.get("cats", []):
            c = CAT_COLOR.get(cat, "#aaa")
            timeline += f'<span style="color:{c};font-family:monospace">{SYM[cat]}</span>'

        rows += f"""
            <tr style="background:{bg}22">
              <td><b>{icao}</b></td>
              <td>{d['name']}</td>
              <td style="color:{color};font-weight:bold">{rating}</td>
              <td>{min_vis} m</td>
              <td>{fog_h}h</td>
              <td><span style="font-size:11px;color:#aaa">{regime}</span></td>
              <td style="letter-spacing:2px;font-size:14px">{timeline}</td>
            </tr>"""

    # Детайлна таблица по часове
    detail = ""
    for icao, d in airports.items():
        if "error" in d or not d.get("cats"):
            continue
        detail += f"""
        <div class="detail-block">
          <h3>{icao} — {d['name']}</h3>
          <table class="detail-table">
            <tr><th>UTC</th><th>VIS m</th><th>T °C</th><th>CAT</th></tr>"""
        for i, (h, vis, T, cat) in enumerate(zip(
                d["hours_utc"], d["vis"], d["T"], d["cats"])):
            color = CAT_COLOR.get(cat, "#fff")
            detail += f"""
            <tr>
              <td>{h:.0f}</td>
              <td>{vis:.0f}</td>
              <td>{T:.1f}</td>
              <td style="color:{color};font-weight:bold">{cat}</td>
            </tr>"""
        detail += "</table></div>"

    return f"""<!DOCTYPE html>
<html lang="bg">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="3600">
  <title>ДП РВД — Прогноза мъгла</title>
  <style>
    body {{
      background: #0d1b2a;
      color: #c8d8e8;
      font-family: 'Segoe UI', Arial, sans-serif;
      max-width: 960px;
      margin: 0 auto;
      padding: 20px;
    }}
    h1 {{ color: #4fc3f7; border-bottom: 2px solid #2a5a8a; padding-bottom: 10px; }}
    h2 {{ color: #7eb8e0; margin-top: 30px; }}
    h3 {{ color: #4fc3f7; }}
    .run-time {{ color: #6a9ab0; font-size: 13px; margin-bottom: 20px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
    th {{
      background: #1a3a5a;
      color: #7eb8e0;
      padding: 10px 14px;
      text-align: left;
      font-size: 13px;
    }}
    td {{ padding: 10px 14px; border-bottom: 1px solid #1a3a5a; font-size: 14px; }}
    tr:hover {{ background: #1a2a3a44; }}
    .legend {{
      display: flex; gap: 20px; margin: 15px 0;
      font-size: 13px; flex-wrap: wrap;
    }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; }}
    .legend-dot {{
      width: 14px; height: 14px; border-radius: 3px;
    }}
    .detail-block {{
      display: inline-block; vertical-align: top;
      margin: 10px; width: 180px;
    }}
    .detail-table td, .detail-table th {{
      padding: 5px 8px; font-size: 12px;
    }}
    .footer {{
      margin-top: 40px; color: #456; font-size: 12px;
      border-top: 1px solid #1a3a5a; padding-top: 10px;
    }}
  </style>
</head>
<body>

<h1>Airport Fog Nowcasting</h1>
<div class="run-time">Последен рун: {run_time} | Следващ: автоматично след 1 час</div>

<div class="legend">
  <div class="legend-item">
    <div class="legend-dot" style="background:#ff4444"></div> LIFR &lt;200m
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#ff8800"></div> IFR 200-600m
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#ffcc00"></div> MVFR 600-1000m
  </div>
  <div class="legend-item">
    <div class="legend-dot" style="background:#44cc44"></div> VFR &gt;1000m
  </div>
</div>

<h2>Обобщена прогноза +12h</h2>
<table>
  <tr>
    <th>ICAO</th>
    <th>Летище</th>
    <th>Категория</th>
    <th>Мин. VIS</th>
    <th>Часове &lt;1000m</th>
    <th>Режим</th>
    <th>Timeline (+12h)</th>
  </tr>
  {rows}
</table>

<h2>Прогноза по часове</h2>
{detail}

<div class="footer">
  1D Fog Model (PAFOG-type) |
  Данни: ICON-EU (Open-Meteo) + METAR (aviationweather.gov)
</div>

</body>
</html>"""


if __name__ == "__main__":
    main()
