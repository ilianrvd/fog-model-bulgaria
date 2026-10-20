"""
test_operational_regime.py
==========================
Приемателен тест (в) от унификацията 20.10.2026.

Пуска ОПЕРАТИВНИЯ път (run_operational.run_airport) върху исторически
случаи от набора — с кеширан ICON от icon_cache/ и стартов METAR от
cases/ — и го сравнява час по час с ВЕРИФИЦИРАНИЯ път:

  1. режимна последователност  vs  regime_changes[] в diagnostic_summary.json
  2. режимна последователност  vs  verify_cases.run_model (жив пробег)
  3. T_sfc и VIS_sfc по часове vs  verify_cases.run_model (трябва да са
     БИТ-ИДЕНТИЧНИ — същият вход, същият код)

Без мрежа: ако профилът не е в icon_cache/, тестът спира с грешка
вместо да тегли.

Употреба (средата е задължителна, третият ред също):
    set D1_DOOR=0
    set D2_SOIL=1
    echo %D1_DOOR% %D2_SOIL% %D2_SOIL_MAX_KHR%
    python test_operational_regime.py
    python test_operational_regime.py LBWN_CDRY_2025-01-23 LBBG_DYNM_2025-03-14
"""

import copy
import json
import os
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify_cases as vc
import run_operational as ro

# По подразбиране: LBWN и LBBG със смени на режима (вкл. двойния критерий
# и ADVECTIVE), плюс един континентален за контрол.
DEFAULT_CASES = [
    "LBWN_CDRY_2025-01-23",   # dynamic 20 → radiative 22 → dynamic 06
    "LBBG_DYNM_2025-03-14",   # advective 22 → dynamic 00
    "LBBG_DYNM_2026-07-27",   # radiative 21 → advective 03 → dynamic 06
]
OP_HOURS = 12               # оперативният хоризонт (18 → 06 UTC)


def _cached_profile(icao, date_str, hour):
    key  = f"{icao}_{date_str}_{hour:02d}_{vc.FORECAST_H + 1}"
    path = os.path.join(vc.ICON_CACHE, key + ".json")
    if not os.path.exists(path):
        raise SystemExit(f"ЛИПСВА кеш: {path} — тестът не тегли от мрежата.")
    return vc.fetch_icon_cached(icao, date_str, hour, vc.FORECAST_H + 1)


def _start_metar(obs_list, date_str, hour):
    """Същият избор като verify_cases.run_model (±45 мин от старта)."""
    t0 = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, tzinfo=timezone.utc)
    near = [o for o in obs_list if abs((o["dt"] - t0).total_seconds()) <= 2700]
    if not near:
        return ""
    return min(near, key=lambda o: abs((o["dt"] - t0).total_seconds()))["raw"]


def _hourly_from_log(regime_log, hour0, n_hours):
    """regime_log ([{hour_utc, regime}]) → режим на всеки цял час."""
    seq = []
    for k in range(n_hours + 1):
        cur = regime_log[0]["regime"]
        for e in regime_log[1:]:
            if (e["hour_utc"] - hour0) % 24 <= k + 1e-6:
                cur = e["regime"]
        seq.append(cur)
    return seq


def _hourly_from_summary(rec, hour0, n_hours):
    log = [{"hour_utc": hour0, "regime": rec["regime_start"]}]
    log += [{"hour_utc": c["hour_utc"], "regime": c["to"]}
            for c in rec.get("regime_changes", [])]
    return _hourly_from_log(log, hour0, n_hours)


def _at_hours(history, n_hours):
    by_t = {round(r["time_h"], 2): r for r in history}
    return [by_t.get(float(k)) for k in range(n_hours + 1)]


def check_case(case_id, summary):
    path = os.path.join(vc.CASES_DIR, case_id + ".txt")
    icao, _cat, date_str, obs = vc.load_case_file(path)
    hour = vc.START_HOUR

    profile   = _cached_profile(icao, date_str, hour)
    metar_raw = _start_metar(obs, date_str, hour)

    # Оперативен път — кеширан профил, историческа дата
    now = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, minute=20, tzinfo=timezone.utc)
    hist_op, _r, _why, reg_op = ro.run_airport(
        icao, metar_raw, hours=OP_HOURS, dt=60,
        profile=copy.deepcopy(profile), now=now)

    # Верифициран път — жив пробег от кеша
    hist_vc, log_vc = vc.run_model(icao, date_str, hour, obs)
    reg_vc = _hourly_from_log(log_vc, hour, OP_HOURS)

    rec = summary.get(case_id)
    reg_sm = _hourly_from_summary(rec, hour, OP_HOURS) if rec else None

    ok = True
    print(f"\n{case_id}   METAR: {metar_raw[:50]}")
    print(f"  {'UTC':>3}  {'ОПЕРАТ.':<10} {'VERIFY':<10} {'SUMMARY':<10} "
          f"{'ΔT_sfc':>8} {'ΔVIS':>7}")
    op_h, vc_h = _at_hours(hist_op, OP_HOURS), _at_hours(hist_vc, OP_HOURS)
    for k in range(OP_HOURS + 1):
        h  = (hour + k) % 24
        sm = reg_sm[k] if reg_sm else "—"
        a, b = op_h[k], vc_h[k]
        dT = (a["T_sfc"] - b["T_sfc"]) if (a and b) else float("nan")
        dV = (a["vis_sfc"] - b["vis_sfc"]) if (a and b) else float("nan")
        bad = (reg_op[k] != reg_vc[k]
               or (reg_sm and reg_op[k] != sm)
               or not (a and b) or dT != 0.0 or dV != 0.0)
        ok &= not bad
        print(f"  {h:02d}   {reg_op[k]:<10} {reg_vc[k]:<10} {sm:<10} "
              f"{dT:+8.4f} {dV:+7.1f}  {'✗' if bad else '✓'}")
    print(f"  → {'СЪВПАДА' if ok else 'РАЗМИНАВАНЕ'}")
    return ok


def main():
    env = {k: os.environ.get(k, "(не е зададен)")
           for k in ("D1_DOOR", "D2_SOIL", "D2_SOIL_MAX_KHR")}
    print("Среда:", env)
    if os.environ.get("D2_SOIL") != "1":
        raise SystemExit("D2_SOIL не е 1 — некоректна среда, спирам.")

    with open("diagnostic_summary.json", encoding="utf-8") as f:
        summary = {r["case"]: r for r in json.load(f)}

    cases = sys.argv[1:] or DEFAULT_CASES
    results = {c: check_case(c, summary) for c in cases}
    print("\n" + "═" * 50)
    for c, ok in results.items():
        print(f"  {c:<26} {'✓ СЪВПАДА' if ok else '✗ РАЗМИНАВАНЕ'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
