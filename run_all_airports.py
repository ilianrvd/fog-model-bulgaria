"""
run_all_airports.py
===================
Оперативен скрипт — пуска 1D модела за мъгла за всички 5 летища
с реални данни от ICON-EU (Open-Meteo) и METAR (aviationweather.gov).

Употреба
--------
python run_all_airports.py                    # всички 5 летища
python run_all_airports.py LBSF LBWN         # само избрани
python run_all_airports.py --hours 12         # прогнозен хоризонт
python run_all_airports.py --no-nudge         # без nudging
"""

import sys, os, argparse, numpy as np
from datetime import datetime, timezone

# Локални модули
sys.path.insert(0, os.path.dirname(__file__))
from fog_model      import FogModel1D, lwc_to_visibility, vis_to_metar_category
from icon_reader    import fetch_icon_eu, AIRPORT_COORDS
from metar_fetcher  import fetch_all_airports
from metar_parser   import parse_metar, apply_metar_correction
from output         import save_surface_csv, print_visibility_timeline, plot_forecast


# ──────────────────────────────────────────────────────────────
# Летищни конфигурации
# ──────────────────────────────────────────────────────────────
AIRPORT_CONFIG = {
    "LBSF": {"coastal": False, "N_d": 300e6, "vis_beta": 100,
              "tau_coastal": None, "river_fq": False},
    "LBWN": {"coastal": True,  "N_d":  50e6, "vis_beta": 144.7,
              "tau_coastal": 3600, "river_fq": False},
    "LBBG": {"coastal": True,  "N_d":  80e6, "vis_beta": 130,
              "tau_coastal": 3600, "river_fq": False},
    "LBPD": {"coastal": False, "N_d": 200e6, "vis_beta": 120,
              "tau_coastal": None, "river_fq": False},
    "LBGO": {"coastal": False, "N_d": 150e6, "vis_beta": 130,
              "tau_coastal": None, "river_fq": True},
}

ALL_AIRPORTS = list(AIRPORT_CONFIG.keys())


# ──────────────────────────────────────────────────────────────
# Диагностика на режима (nudging)
# ──────────────────────────────────────────────────────────────

def diagnose_regime(profile_t0: dict, metar: dict, cfg: dict) -> tuple:
    """
    Определя синоптичния режим и τ_relax.
    Връща: (режим: str, tau: int|None, причина: str)
    """
    V_sfc = (metar.get("wind_speed") or 0) * 0.5144   # kt → m/s

    # Промяна на въздушната маса: t+0 vs t+3h
    profiles = profile_t0.get("hourly_profiles", [])
    dT_col = 0.0
    if len(profiles) >= 3:
        T0 = profiles[0]["T"]
        T3 = profiles[3]["T"] if len(profiles) > 3 else profiles[-1]["T"]
        n  = min(len(T0), len(T3))
        dT_col = float(np.mean(np.abs(T3[:n] - T0[:n])))

    # Устойчивост: инверсия в долните ~200m
    T_prof   = profile_t0["T"]
    z_prof   = profile_t0["z"]
    idx_200  = np.searchsorted(z_prof, 200)
    dtheta   = float(T_prof[min(idx_200, len(T_prof)-1)] - T_prof[0])

    # Решение
    if cfg["coastal"] and V_sfc > 3.0:
        return "advective", cfg["tau_coastal"] or 3600, \
               f"Крайбрежно, V={V_sfc:.1f} m/s → адвекция"

    if dT_col > 2.0:
        return "dynamic", 3600, \
               f"Промяна въздушна маса ΔT={dT_col:.1f} K → nudging τ=1h"

    if V_sfc > 5.0:
        return "dynamic", 3600, \
               f"Силен вятър V={V_sfc:.1f} m/s → nudging τ=1h"

    if V_sfc > 2.0 and dtheta < 1.0:
        return "moderate", 10800, \
               f"Умерен вятър, слаба инверсия → nudging τ=3h"

    if dtheta > 2.0 and V_sfc < 2.0:
        return "radiative", None, \
               f"Инверсия {dtheta:.1f} K, тих вятър → само инициализация"

    return "moderate", 10800, "Неясен режим → консервативен τ=3h"


# ──────────────────────────────────────────────────────────────
# Nudging
# ──────────────────────────────────────────────────────────────

def apply_nudging(model: FogModel1D, wrf_prof: dict, tau_s: float):
    """Плавно въвличане на ICON профила в 1D модела."""
    alpha = model.dt / tau_s

    # Интерполация на ICON профила към мрежата на модела
    T_icon  = np.interp(model.z, wrf_prof["z"], wrf_prof["T"])
    qv_icon = np.interp(model.z, wrf_prof["z"], wrf_prof["qv"])
    u_icon  = np.interp(model.z, wrf_prof["z"], wrf_prof["u"])
    v_icon  = np.interp(model.z, wrf_prof["z"], wrf_prof["v"])

    model.T  += alpha * (T_icon  - model.T)
    model.qv += alpha * (qv_icon - model.qv)
    model.u  += alpha * (u_icon  - model.u)
    model.v  += alpha * (v_icon  - model.v)
    model.qv  = np.maximum(model.qv, 1e-8)
    # ql не се nudge-ва!


# ──────────────────────────────────────────────────────────────
# Прогноза за едно летище
# ──────────────────────────────────────────────────────────────

def run_airport(icao: str, metar_raw: str | None,
                hours: float = 12.0, dt: float = 60.0,
                use_nudging: bool = True, out_dir: str = ".") -> list:
    """Пуска 1D модела за едно летище. Връща history."""

    cfg = AIRPORT_CONFIG[icao]
    print(f"\n{'═'*60}")
    print(f"  {icao}  {AIRPORT_COORDS[icao]['name']}")
    print(f"{'═'*60}")

    # 1. ICON-EU профил
    profile = fetch_icon_eu(icao, forecast_hours=int(hours) + 1)

    # 2. METAR корекция
    metar_dict = {}
    if metar_raw:
        metar_dict = parse_metar(metar_raw)
        profile    = apply_metar_correction(profile, metar_dict)
    else:
        profile.setdefault("ql_init", np.zeros_like(profile["T"]))

    # 3. Режим
    regime, tau, reason = diagnose_regime(profile, metar_dict, cfg)
    print(f"[РЕЖИМ] {regime.upper()}  —  {reason}")
    if tau:
        print(f"[РЕЖИМ] τ_relax = {tau//3600}h")
    else:
        print(f"[РЕЖИМ] Без nudging")

    # 4. Инициализация
    # Интерполиране на профила към равномерна мрежа 40 нива 0-2000m
    z_model = np.linspace(2.0, 2000.0, 40)
    T_m  = np.interp(z_model, profile["z"], profile["T"])
    qv_m = np.interp(z_model, profile["z"], profile["qv"])
    p_m  = np.interp(z_model, profile["z"], profile["p"])
    u_m  = np.interp(z_model, profile["z"], profile["u"])
    v_m  = np.interp(z_model, profile["z"], profile["v"])

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=profile["hour0"], dt=dt)
    model.ql = np.interp(z_model, profile["z"],
                          profile.get("ql_init", np.zeros_like(profile["z"])))

    # 5. Интеграция с nudging
    steps_total  = int(hours * 3600 / dt)
    nudge_every  = int(3600 / dt)   # на всеки 1 час
    hourly_profs = profile.get("hourly_profiles", [])
    nudge_idx    = 1                # започваме от t+1h

    model.diagnose()
    r0 = model.history[-1]
    print(f"\n{'Час UTC':>8} | {'T°C':>6} | {'RH%':>5} | {'LWC g/m³':>9} | {'VIS m':>7} | CAT")
    print("-" * 55)
    print(f"{r0['hour_utc']:8.1f} | {r0['T_sfc']-273.15:6.1f} | "
          f"{r0['rh_sfc']*100:5.1f} | {r0['ql_sfc']*1000:9.4f} | "
          f"{r0['vis_sfc']:7.0f} | {r0['cat']}")

    for step in range(1, steps_total + 1):
        model.step()

        # Nudging на всеки час
        if use_nudging and tau and step % nudge_every == 0:
            if nudge_idx < len(hourly_profs):
                apply_nudging(model, hourly_profs[nudge_idx], tau)
                nudge_idx += 1

        # Изход на всеки час
        if step % nudge_every == 0:
            r = model.diagnose()
            print(f"{r['hour_utc']:8.1f} | {r['T_sfc']-273.15:6.1f} | "
                  f"{r['rh_sfc']*100:5.1f} | {r['ql_sfc']*1000:9.4f} | "
                  f"{r['vis_sfc']:7.0f} | {r['cat']}")

    # 6. Изход
    os.makedirs(out_dir, exist_ok=True)
    save_surface_csv(model.history,
                     out_path=os.path.join(out_dir, f"{icao}_fog_forecast.csv"))
    plot_forecast(model.history,
                  out_png=os.path.join(out_dir, f"{icao}_fog_forecast.png"))
    return model.history


# ──────────────────────────────────────────────────────────────
# Обобщена таблица
# ──────────────────────────────────────────────────────────────

def print_summary(all_results: dict):
    SYM = {"LIFR": "█", "IFR": "▓", "MVFR": "░", "VFR": "·"}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\n\n{'═'*70}")
    print(f"  ОБОБЩЕНА ПРОГНОЗА МЪГЛА — {now}")
    print(f"{'═'*70}")
    print(f"  {'ICAO':<6} {'Летище':<16} {'Мин.VIS':>8} {'@UTC':>5} {'<1000m':>6}   Оценка")
    print(f"  {'-'*66}")

    for icao, history in all_results.items():
        if not history:
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

    print(f"{'═'*70}")
    print(f"\n  Timeline  (█LIFR  ▓IFR  ░MVFR  ·VFR)")
    for icao, history in all_results.items():
        if not history:
            continue
        bar = " ".join(SYM[r["cat"]] for r in history)
        print(f"  {icao:<5}  {bar}")
    print()


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="1D Fog Model — всички летища, реални данни ICON-EU + METAR")
    parser.add_argument("airports", nargs="*", default=ALL_AIRPORTS,
                        help="ICAO кодове (default: всички 5)")
    parser.add_argument("--hours",    type=float, default=12.0)
    parser.add_argument("--dt",       type=float, default=60.0)
    parser.add_argument("--no-nudge", action="store_true")
    parser.add_argument("--out",      type=str,   default="fog_output")
    args = parser.parse_args()

    airports = [a.upper() for a in args.airports if a.upper() in AIRPORT_CONFIG]
    if not airports:
        print("Невалидни ICAO кодове. Налични: " + ", ".join(ALL_AIRPORTS))
        sys.exit(1)

    print(f"\n{'═'*60}")
    print(f"  ДП РВД — 1D МОДЕЛ МЪГЛА  (ICON-EU + METAR)")
    print(f"  Летища: {', '.join(airports)}")
    print(f"  Прогноза: {args.hours:.0f}h  |  dt={args.dt:.0f}s")
    print(f"{'═'*60}")

    # Изтегли всички METAR наведнъж
    try:
        metars = fetch_all_airports(airports)
    except Exception as e:
        print(f"[ВНИМАНИЕ] METAR грешка: {e} — продължавам без METAR")
        metars = {}

    # Пусни модела за всяко летище
    all_results = {}
    for icao in airports:
        try:
            history = run_airport(
                icao         = icao,
                metar_raw    = metars.get(icao),
                hours        = args.hours,
                dt           = args.dt,
                use_nudging  = not args.no_nudge,
                out_dir      = args.out,
            )
            all_results[icao] = history
        except Exception as e:
            print(f"\n[ГРЕШКА] {icao}: {e}")
            all_results[icao] = []

    # Обобщена таблица
    print_summary(all_results)
    print(f"[ИЗХОД] Файлове записани в: {args.out}/")


if __name__ == "__main__":
    main()
