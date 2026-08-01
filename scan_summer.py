# -*- coding: utf-8 -*-
"""
scan_summer.py — разузнаване за летни FA
=========================================
Пуска модела за поредица летни нощи и сравнява със METAR-ите, за да
намери нощ, в която моделът дава мъгла, а наблюдението е ясно.

    python scan_summer.py 2026-07-01 2026-07-28
    python scan_summer.py 2026-07-01 2026-07-28 --airport LBSF
    python scan_summer.py 2026-06-01 2026-06-30 --old      # със стария модел

Пази изхода в scan_summer_<ICAO>.json, за да не се губи при прекъсване,
и продължава оттам, ако го пуснеш пак.

ВНИМАНИЕ: иска мрежа (open-meteo + OGIMET). При таймаут пропуска нощта
и продължава; в края казва кои са пропуснати.
"""
import sys, os, json, argparse, io, contextlib, traceback
from datetime import datetime, timedelta

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ap = argparse.ArgumentParser()
ap.add_argument("start")
ap.add_argument("end")
ap.add_argument("--airport", default="LBSF")
ap.add_argument("--hour", type=int, default=18)
ap.add_argument("--hours", type=float, default=15.0)
ap.add_argument("--quiet", action="store_true",
                help="скрива подробния изход на модела")
args = ap.parse_args()

import run_case as rc
from ogimet_fetcher import fetch_metar_ogimet
import pairing

ICAO = args.airport
STATE = f"scan_summer_{ICAO}.json"
done = {}
if os.path.exists(STATE):
    done = json.load(open(STATE, encoding="utf-8"))
    print(f"[продължавам] {len(done)} нощи вече обработени")

d0 = datetime.strptime(args.start, "%Y-%m-%d")
d1 = datetime.strptime(args.end,   "%Y-%m-%d")
dates = [(d0 + timedelta(days=k)).strftime("%Y-%m-%d")
         for k in range((d1 - d0).days + 1)]

skipped = []

def quietly(fn, *a, **kw):
    """Връща (резултат, прихванат_изход)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    out = buf.getvalue()
    if not args.quiet:
        print(out, end="")
    return r, out


import re as _re
def _grab(txt):
    """Изважда T_soil и режима от изхода на run_case."""
    d = {}
    m = _re.search(r"\[SOIL\] T_soil от ICON:\s*([-\d.]+)", txt)
    if m: d["T_soil_icon"] = float(m.group(1))
    m = _re.search(r"T_air=([-\d.]+)", txt)
    if m: d["T_air_init"] = float(m.group(1))
    reg = _re.findall(r"\[РЕЖИМ[^\]]*\]\s*(.{0,70})", txt)
    if reg: d["regime"] = reg[0].strip()
    d["regimes_n"] = len(reg)
    return d

for ds in dates:
    if ds in done:
        continue
    print(f"\n─── {ICAO} {ds} {args.hour:02d} UTC ───", flush=True)
    try:
        obs, _ = quietly(fetch_metar_ogimet, ICAO, ds,
                         hour0=args.hour, hours=int(args.hours) + 2)
        if not obs:
            skipped.append((ds, "няма METAR"))
            continue
        # начален METAR — най-близък до hour0
        start_raw = obs[0]["raw"]
        hist, log = quietly(rc.run_case, ICAO, ds, args.hour, start_raw,
                            hours=args.hours, use_nudging=True,
                            out_dir="scan_output")
        if not hist:
            skipped.append((ds, "празна история"))
            continue

        mod_min = min(r["vis_sfc"] for r in hist)
        obs_v   = [o["vis_m"] for o in obs if o.get("vis_m") is not None]
        obs_min = min(obs_v) if obs_v else None
        t_mod   = [r["T_sfc"] - 273.15 for r in hist]
        t_obs   = [o["T"] for o in obs if o.get("T") is not None]

        # най-влажният момент в наблюденията
        spreads = [o["T"] - o["Td"] for o in obs
                   if o.get("T") is not None and o.get("Td") is not None]
        i_min = min(range(len(hist)), key=lambda k: hist[k]["vis_sfc"])
        rec = dict(_grab(log))
        rec.update({
            "mod_min_vis": float(mod_min),
            "mod_ql_max" : float(max(r["ql_sfc"] for r in hist)),
            "rh_at_min"  : float(hist[i_min]["rh_sfc"]),
            "hour_at_min": float(hist[i_min]["hour_utc"]),
            "obs_spread_min": None if not spreads else float(min(spreads)),
            "obs_min_vis": None if obs_min is None else float(obs_min),
            "mod_Tmin"   : float(min(t_mod)),
            "obs_Tmin"   : None if not t_obs else float(min(t_obs)),
            "n_obs"      : len(obs),
        })
        rec["Tmin_err"] = (None if rec["obs_Tmin"] is None
                           else rec["mod_Tmin"] - rec["obs_Tmin"])
        fog_mod = mod_min < 2000
        fog_obs = obs_min is not None and obs_min < 2000
        rec["verdict"] = ("FA" if fog_mod and not fog_obs else
                          "HIT" if fog_mod and fog_obs else
                          "MISS" if fog_obs and not fog_mod else "CN")
        done[ds] = rec
        json.dump(done, open(STATE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"  → {rec['verdict']}  VIS модел {mod_min:.0f} m / "
              f"набл. {obs_min if obs_min is None else int(obs_min)} m   "
              f"Tmin_err {rec['Tmin_err'] if rec['Tmin_err'] is None else round(rec['Tmin_err'],1)}",
              flush=True)
    except KeyboardInterrupt:
        print("\n[прекъснато] състоянието е запазено")
        break
    except Exception as e:
        skipped.append((ds, str(e)[:60]))
        print(f"  ГРЕШКА: {e}", flush=True)

# ── Резюме
print(f"\n{'='*74}")
print(f"  {ICAO}  {args.start} → {args.end}   обработени {len(done)} нощи")
print(f"{'='*74}")
print(f"  {'дата':<12} {'VIS мод':>8} {'VIS набл':>9} {'Tmin мод':>9} "
      f"{'Tmin набл':>10} {'Tmin_err':>9}  присъда")
for ds in sorted(done):
    r = done[ds]
    om = "—" if r["obs_min_vis"] is None else f"{r['obs_min_vis']:.0f}"
    ot = "—" if r["obs_Tmin"] is None else f"{r['obs_Tmin']:.1f}"
    te = "—" if r["Tmin_err"] is None else f"{r['Tmin_err']:+.1f}"
    mark = "  ← КАНДИДАТ" if r["verdict"] == "FA" else ""
    print(f"  {ds:<12} {r['mod_min_vis']:>8.0f} {om:>9} "
          f"{r['mod_Tmin']:>9.1f} {ot:>10} {te:>9}  {r['verdict']}{mark}")

fa = [d for d, r in done.items() if r["verdict"] == "FA"]
errs = [r["Tmin_err"] for r in done.values() if r["Tmin_err"] is not None]
print(f"\n  FA нощи: {len(fa)}" + (f"  → {', '.join(sorted(fa))}" if fa else ""))
if errs:
    print(f"  Tmin_err: среден {sum(errs)/len(errs):+.2f} K   "
          f"мин {min(errs):+.2f}   макс {max(errs):+.2f}")
    cold = sum(1 for e in errs if e < -1.0)
    print(f"  нощи с преохлаждане > 1 K: {cold} от {len(errs)}")
if skipped:
    print(f"\n  пропуснати: {len(skipped)}")
    for ds, why in skipped[:10]:
        print(f"    {ds}  {why}")
