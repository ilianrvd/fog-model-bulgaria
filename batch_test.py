"""
batch_test.py
=============
Автоматично пускане на архивни случаи за верификация на fog модела.
Записва подробен лог в logs/ за анализ.

Употреба:
    python batch_test.py                    # всички случаи
    python batch_test.py --airport LBSF     # само LBSF
    python batch_test.py --list             # показва списъка

Изход:
    logs/batch_YYYY-MM-DD.json   — пълен машинно-четим лог
    logs/batch_YYYY-MM-DD.txt    — четим текстов отчет
"""

import sys, os, json, argparse
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

# ──────────────────────────────────────────────────────────────────────────────
# Списък с тестови случаи
# Формат: (дата, час UTC, летище, бележка)
# Подбрани да покриват различни синоптични ситуации и всички летища
# ──────────────────────────────────────────────────────────────────────────────
CASES = [
    # LBSF — различни типове
    ("2024-12-30", 18, "LBSF", "Класическа радиационна FZFG — еталон"),
    ("2025-01-02", 18, "LBSF", "Мъгла при старт, незасичане след"),
    ("2024-01-18", 18, "LBSF", "Суха зимна нощ — T грешка преди SEB"),
    ("2024-11-16", 18, "LBSF", "Адвективно охлаждане от облачност"),
    ("2024-10-07", 18, "LBSF", "Топла есенна почва — T замразена"),
    ("2024-10-13", 18, "LBSF", "Мъглообразуване след полунощ"),
    ("2024-10-21", 18, "LBSF", "Октомври — системна проверка"),
    ("2024-10-25", 18, "LBSF", "Октомври — системна проверка 2"),
    ("2024-12-28", 18, "LBSF", "Суха нощ — FA очакван"),
    ("2024-12-29", 18, "LBSF", "VFR нощ — без мъгла"),
    ("2024-12-25", 18, "LBSF", "VFR нощ — без мъгла 2"),

    # LBWN — крайбрежно, морска мъгла
    ("2024-12-30", 18, "LBWN", "Морска мъгла Варна — адвективна"),
    ("2024-12-29", 18, "LBWN", "LBWN проверка"),
    ("2024-12-25", 18, "LBWN", "LBWN проверка 2"),

    # LBBG — Бургас
    ("2024-12-30", 18, "LBBG", "Бургас — крайбрежно"),
    ("2024-12-28", 18, "LBBG", "Бургас — проверка"),

    # LBPD — Пловдив
    ("2024-12-30", 18, "LBPD", "Пловдив — радиационна"),
    ("2024-10-13", 18, "LBPD", "Пловдив — есен"),

    # LBGO — Горна Оряховица
    ("2024-12-30", 18, "LBGO", "Г.Оряховица — радиационна"),
    ("2024-12-28", 18, "LBGO", "Г.Оряховица — проверка"),
]

# ──────────────────────────────────────────────────────────────────────────────

def run_one_case(date_str, hour, icao):
    """Пуска един случай и връща резултатите."""
    from run_case import (fetch_icon_historical, build_surface_layer,
                          diagnose_regime, AIRPORT_CONFIG)
    from fog_model import FogModel1D
    from metar_parser import parse_metar, apply_metar_correction
    from ogimet_fetcher import fetch_metar_ogimet, find_obs_at
    from ogimet_fetcher import verify_forecast

    cfg = AIRPORT_CONFIG[icao]
    doy = datetime.strptime(date_str, "%Y-%m-%d").timetuple().tm_yday

    # METAR
    obs_list = fetch_metar_ogimet(icao, date_str, hour0=max(hour-2,0),
                                   hours=14, sleep_s=25)
    all_obs_dict = {icao: obs_list}
    obs_at, _, _ = find_obs_at(all_obs_dict, icao, hour, date_str)
    metar_raw  = obs_at.get("raw", "") if obs_at else ""
    metar_dict = parse_metar(metar_raw) if metar_raw else {}

    # ICON
    profile = fetch_icon_historical(icao, date_str, hour0=hour, forecast_hours=13)
    if metar_dict:
        profile = apply_metar_correction(profile, metar_dict)
    profile = build_surface_layer(profile, metar_dict, doy)

    # Режим
    regime, tau, reason = diagnose_regime(profile, metar_dict, cfg)

    # Модел
    import numpy as np
    z_log   = np.logspace(np.log10(0.5), np.log10(50), 20)
    z_lin   = np.linspace(55, 2000, 20)
    z_model = np.concatenate([z_log, z_lin])
    T_m  = np.interp(z_model, profile["z"], profile["T"])
    qv_m = np.interp(z_model, profile["z"], profile["qv"])
    p_m  = np.interp(z_model, profile["z"], profile["p"])
    u_m  = np.interp(z_model, profile["z"], profile["u"])
    v_m  = np.interp(z_model, profile["z"], profile["v"])

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=float(hour), dt=60, day_of_year=doy)

    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        model.T_skin = min(float(T_soil_icon), model.T[0])

    ql_init_raw = profile.get("ql_init")
    if ql_init_raw is not None and len(ql_init_raw) == len(profile["z"]):
        model.ql = np.interp(z_model, profile["z"], ql_init_raw)
    elif ql_init_raw is not None and np.any(np.array(ql_init_raw) > 0):
        model.ql = np.where(z_model < 50, float(np.max(ql_init_raw)), 0.0)
    else:
        model.ql = np.zeros(len(z_model))

    hourly_profs = profile.get("hourly_profiles", [])
    steps_total  = 12 * 60
    steps_per_hr = 60
    dt = 60

    # Hourly reassessment логика
    from fog_model import _sin_elevation
    import io as _io, sys as _sys2
    current_regime = regime
    current_tau    = tau
    pending_regime = None
    pending_count  = 0
    regime_log     = [{"hour_utc": hour, "regime": regime, "reason": reason}]

    from run_case import apply_nudging

    model.diagnose()

    for step in range(1, steps_total + 1):
        model.step()
        hour_elapsed = step * dt / 3600.0
        prof_idx     = min(int(hour_elapsed), len(hourly_profs)-1)

        if step % steps_per_hr == 0 and hourly_profs:
            hour_now  = (float(hour) + hour_elapsed) % 24
            hour_next = (hour_now + 1) % 24
            sin_el      = _sin_elevation(hour_now,  doy)
            sin_el_next = _sin_elevation(hour_next, doy)
            is_sunrise  = sin_el > 0.05 and sin_el_next > sin_el

            remaining = hourly_profs[prof_idx:]
            if len(remaining) < 3:
                remaining = hourly_profs[-3:]
            _old = _sys2.stdout; _sys2.stdout = _io.StringIO()
            cand_regime, cand_tau, cand_reason = diagnose_regime(
                {"hourly_profiles": remaining}, {}, cfg)
            _sys2.stdout = _old

            if is_sunrise and current_regime == "radiative":
                cand_regime = "dynamic"
                cand_tau    = 7200
                cand_reason = f"Изгрев → nudging T"

            if current_regime == "dynamic" and cand_regime == "radiative" and is_sunrise:
                cand_regime = "dynamic"
                cand_tau    = current_tau

            if cand_regime != current_regime:
                pending_regime = cand_regime if cand_regime == pending_regime else cand_regime
                pending_count  = pending_count + 1 if cand_regime == pending_regime else 1
                threshold = 1 if is_sunrise else 2
                if pending_count >= threshold:
                    regime_log.append({
                        "hour_utc": (hour + hour_elapsed) % 24,
                        "regime": cand_regime,
                        "reason": cand_reason
                    })
                    current_regime = cand_regime
                    current_tau    = cand_tau
                    pending_regime = None
                    pending_count  = 0
            else:
                pending_regime = None
                pending_count  = 0

        if current_tau and hourly_profs:
            apply_nudging(model, hourly_profs[prof_idx], cfg["tau_T"], current_tau)

        if step % steps_per_hr == 0:
            model.diagnose()

    # Верификация
    # verify_forecast очаква all_obs като {icao: [obs_list]}
    metrics = verify_forecast(model.history, icao, date_str, hour, all_obs_dict)

    return {
        "history"   : model.history,
        "regime_log": regime_log,
        "metrics"   : metrics,
        "metar_raw" : metar_raw or "",
        "T_soil"    : float(T_soil_icon - 273.15) if T_soil_icon else None,
        "T_init"    : float(T_m[0] - 273.15),
        "qv_init"   : float(qv_m[0] * 1000),
        "U_init"    : float(np.hypot(u_m[0], v_m[0])),
    }


def format_txt_report(results):
    """Генерира четим текстов отчет."""
    lines = []
    lines.append("=" * 72)
    lines.append("BATCH ВЕРИФИКАЦИЯ — fog-model-bulgaria")
    lines.append(f"Генериран: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 72)

    # Обобщена таблица
    lines.append(f"\n{'ДАТА':>12} {'ICAO':>6} {'CSI':>6} {'POD':>6} {'FAR':>6} "
                 f"{'MAE m':>7} {'РЕЖИМ':>10} {'БЕЛЕЖКА'}")
    lines.append("-" * 80)

    by_airport = {}
    for r in results:
        if "error" in r:
            lines.append(f"  {r['date']:>12} {r['icao']:>6}  ГРЕШКА: {r['error']}")
            continue

        m   = r["metrics"]
        csi = f"{m['CSI']:.2f}" if m['CSI'] is not None else " nan"
        pod = f"{m['POD']:.2f}" if m['POD'] is not None else " nan"
        far = f"{m['FAR']:.2f}" if m['FAR'] is not None else " nan"
        mae = f"{m['MAE']:.0f}" if m['MAE'] is not None else "   -"
        reg = r["regime_log"][0]["regime"].upper()[:10] if r["regime_log"] else "?"

        lines.append(f"  {r['date']:>12} {r['icao']:>6} {csi:>6} {pod:>6} {far:>6} "
                     f"{mae:>7} {reg:>10}  {r['note']}")

        icao = r["icao"]
        if icao not in by_airport:
            by_airport[icao] = []
        if m["CSI"] is not None:
            by_airport[icao].append(m["CSI"])

    # Средно по летище
    lines.append("\n" + "-" * 40)
    lines.append("Средно CSI по летище:")
    for icao, vals in sorted(by_airport.items()):
        if vals:
            lines.append(f"  {icao}: {np.mean(vals):.3f}  (n={len(vals)})")

    # Детайли по случай
    lines.append("\n" + "=" * 72)
    lines.append("ДЕТАЙЛИ ПО СЛУЧАЙ")
    lines.append("=" * 72)

    for r in results:
        if "error" in r:
            continue
        lines.append(f"\n{'─'*60}")
        lines.append(f"  {r['date']} {r['hour']:02d}UTC — {r['icao']}  ({r['note']})")
        lines.append(f"  Начални условия: T={r['T_init']:.1f}°C  qv={r['qv_init']:.2f}g/kg  "
                     f"U={r['U_init']:.1f}m/s  T_soil={r['T_soil']:.1f}°C" if r['T_soil'] else "")
        lines.append(f"  Режими: " + " → ".join(
            f"{rl['hour_utc']:.0f}UTC:{rl['regime'].upper()}" for rl in r["regime_log"]))
        m = r["metrics"]
        lines.append(f"  Метрики: CSI={m['CSI']:.2f}  POD={m['POD']:.2f}  "
                     f"FAR={m['FAR']:.2f}  MAE={m['MAE']:.0f}m  "
                     f"Hits={m['hits']}  Misses={m['misses']}  FA={m['false_alarms']}")

        # T сравнение
        lines.append(f"  {'UTC':>4} {'T_mod':>7} {'T_obs':>7} {'ΔT':>6} {'VIS_mod':>8} {'VIS_obs':>8}")
        for h in r["history"]:
            obs_h = next((o for o in r.get("obs_list",[]) if
                         abs(int(o["time"][11:13]) - int(h["hour_utc"])) < 1), None)
            T_obs = obs_h["T"] if obs_h else None
            V_obs = obs_h["vis"] if obs_h else None
            T_mod = h["T_sfc"] - 273.15
            dT    = f"{T_mod - T_obs:+.1f}" if T_obs is not None else "  ?"
            lines.append(f"  {h['hour_utc']:4.0f} {T_mod:7.1f} "
                         f"{T_obs:7.1f}" if T_obs else f"  {h['hour_utc']:4.0f} {T_mod:7.1f}{'':>7}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Batch тест на fog модела")
    parser.add_argument("--airport", help="Само едно летище")
    parser.add_argument("--list",    action="store_true", help="Показва случаите")
    parser.add_argument("--date",    help="Само тази дата")
    args = parser.parse_args()

    cases = CASES
    if args.list:
        print(f"{'#':>3} {'Дата':>12} {'Час':>4} {'ICAO':>6}  Бележка")
        for i, (d, h, ic, note) in enumerate(cases, 1):
            print(f"{i:3d} {d:>12} {h:4d} {ic:>6}  {note}")
        return

    if args.airport:
        cases = [(d,h,ic,n) for d,h,ic,n in cases if ic == args.airport.upper()]
    if args.date:
        cases = [(d,h,ic,n) for d,h,ic,n in cases if d == args.date]

    os.makedirs("logs", exist_ok=True)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    json_path = f"logs/batch_{run_date}.json"
    txt_path  = f"logs/batch_{run_date}.txt"

    results = []
    total = len(cases)

    for i, (date_str, hour, icao, note) in enumerate(cases, 1):
        print(f"\n[{i}/{total}] {date_str} {hour:02d}UTC {icao} — {note}")
        try:
            import time; t0 = time.time()
            r = run_one_case(date_str, hour, icao)
            elapsed = time.time() - t0
            m = r["metrics"]
            print(f"  ✓ CSI={m['CSI']:.2f}  POD={m['POD']:.2f}  "
                  f"FAR={m['FAR']:.2f}  MAE={m['MAE']:.0f}m  ({elapsed:.0f}s)")
            results.append({
                "date": date_str, "hour": hour, "icao": icao, "note": note,
                **r
            })
        except Exception as e:
            print(f"  ✗ ГРЕШКА: {e}")
            results.append({"date": date_str, "hour": hour, "icao": icao,
                            "note": note, "error": str(e)})

    # Записваме JSON
    # Конвертираме numpy типове
    def _conv(o):
        if isinstance(o, (np.integer,)): return int(o)
        if isinstance(o, (np.floating,)): return float(o)
        if isinstance(o, np.ndarray): return o.tolist()
        raise TypeError(type(o))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=_conv)
    print(f"\n[OK] JSON: {json_path}")

    # Записваме текст
    txt = format_txt_report(results)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"[OK] TXT:  {txt_path}")

    # Финално резюме
    valid = [r for r in results if "error" not in r]
    if valid:
        csi_vals = [r["metrics"]["CSI"] for r in valid if r["metrics"]["CSI"] is not None]
        print(f"\n{'='*50}")
        print(f"Случаи: {len(valid)}/{total}  Средно CSI: {np.mean(csi_vals):.3f}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
