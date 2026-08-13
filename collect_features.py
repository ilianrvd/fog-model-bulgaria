# -*- coding: utf-8 -*-
"""
collect_features.py — един пробег, всички признаци
===================================================
Пуска целия набор и записва за всяка нощ достатъчно, за да търсим
режимен разделител ПОСЛЕ, без да пускаме модела отново.

    python collect_features.py                    # всички случаи
    python collect_features.py --airport LBGO     # само едно летище
    python collect_features.py --limit 20         # проба

Изход:
    features.json  — всичко, вложено (профили, часови редове)
    features.csv   — плоска таблица по случай, за бърз анализ

Пази състояние след всеки случай. При прекъсване продължава оттам.

Какво се записва
----------------
ICON суров профил      : T, qv, RH на 10/50/100/200/300/500/1000 m
Коригиран профил       : същите височини, след METAR корекция и
                         построяване на приземния слой
Начален METAR          : вятър, T, Td, облачност
Почва                  : T_soil от ICON
Облачност              : серията по часове
Моделен ход            : T, RH, qv, ql, видимост на всеки час
Наблюдение             : минимална видимост, часове с мъгла, брой METAR-и
Оценка                 : събитие, моделна минимална видимост, MAE_T

Защо точно тези
---------------
Хипотезата за проверка: сухите ясни нощи се различават от мъглените
по НАЧАЛНАТА влага — приземна в 18–22 UTC, или във влажностния профил
от ICON. Досега това не е измервано върху пълния набор; наличните
данни са от 19 случая, а METAR спредът е негоден (T и Td се докладват
в цели градуси → T−Td = 0.0 при 18 от 19).
"""
import sys, os, io, json, csv, argparse, contextlib, glob, re
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ap = argparse.ArgumentParser()
ap.add_argument("--cases-dir", default="cases")
ap.add_argument("--airport", default=None)
ap.add_argument("--limit", type=int, default=None)
ap.add_argument("--out", default="features.json")
ap.add_argument("--csv", default="features.csv")
ap.add_argument("--restart", action="store_true",
                help="започва отначало вместо да продължи")
opt = ap.parse_args()

import fog_model as fm
import verify_cases as vc
import run_case as rc

# ── прихващане на двата профила ─────────────────────────────
_CAP = {}
_icon_orig = vc.fetch_icon_cached
_bsl_orig = rc.build_surface_layer


def _icon_cap(*a, **kw):
    p = _icon_orig(*a, **kw)
    _CAP["icon"] = p
    return p


def _bsl_cap(*a, **kw):
    p = _bsl_orig(*a, **kw)
    _CAP["final"] = p
    return p


vc.fetch_icon_cached = _icon_cap
rc.build_surface_layer = _bsl_cap
# verify_cases внася build_surface_layer вътре в run_model, тоест
# ще вземе закърпената версия от модула run_case при всяко викане.

LEVELS = [10.0, 50.0, 100.0, 200.0, 300.0, 500.0, 1000.0]


def rh_of(T, p, qv):
    """Относителна влажност от смесително отношение."""
    qs = fm.sat_mixing_ratio(np.asarray(T, dtype=float),
                             np.asarray(p, dtype=float))
    return np.asarray(qv, dtype=float) / (qs + 1e-12)


def sample(prof, tag):
    """T, qv, RH на фиксирани височини от профил."""
    out = {}
    if not prof or "z" not in prof:
        return out
    z = np.asarray(prof["z"], dtype=float)
    T = np.asarray(prof["T"], dtype=float)
    qv = np.asarray(prof["qv"], dtype=float)
    p = np.asarray(prof.get("p", 1e5 * np.exp(-z / 8400.0)), dtype=float)
    rh = rh_of(T, p, qv)
    for lv in LEVELS:
        if lv < z[0] or lv > z[-1]:
            continue
        out[f"{tag}_T_{lv:.0f}"] = float(np.interp(lv, z, T)) - 273.15
        out[f"{tag}_qv_{lv:.0f}"] = float(np.interp(lv, z, qv)) * 1000.0
        out[f"{tag}_rh_{lv:.0f}"] = float(np.interp(lv, z, rh))
    return out


def run_one(path):
    _CAP.clear()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        icao, cat, ds, obs = vc.load_case_file(path)
        hist, regime = vc.run_model(icao, ds, vc.START_HOUR, obs)
        ev = vc.evaluate(hist, obs, vc.START_HOUR, ds)
        # Изключване на валежно-доминирани случаи — СЪЩИЯТ класификатор,
        # който verify_cases.py ползва за гейта (282 оценявани + 6
        # изключени, репер v26-D2-soil-clip). Преди тази поправка
        # collect_features.py въобще не го викаше и features.csv носеше
        # старата 288-конвенция.
        excluded, excluded_reason, _ = vc.diagnose_obs_cause(obs)

    # моделен ход по часове
    hourly = []
    for r in hist:
        h = r["hour_utc"]
        if abs(h - round(h)) > 0.01:
            continue
        hourly.append(dict(
            hour=float(h),
            T=float(r["T_sfc"]) - 273.15,
            rh=float(r["rh_sfc"]),
            qv=float(r["qv"][0]) * 1000.0,
            ql=float(r["ql_sfc"]) * 1000.0,
            vis=float(r["vis_sfc"])))

    # наблюдение
    ov = [o["vis_m"] for o in obs if o.get("vis_m") is not None]
    ot = [o["T"] for o in obs if o.get("T") is not None]
    od = [o["Td"] for o in obs if o.get("Td") is not None]
    n_fog_obs = sum(1 for v in ov if v < 1000)

    fin = _CAP.get("final", {})
    rec = dict(
        case=os.path.splitext(os.path.basename(path))[0],
        icao=icao, cat=cat, date=ds,
        event=ev["event"],
        excluded=excluded, excluded_reason=excluded_reason,
        mod_min_vis=float(ev["mod_min_vis"]),
        T_MAE=None if ev["T"]["MAE"] is None else float(ev["T"]["MAE"]),
        onset=ev.get("onset_dt_h"),
        obs_min_vis=min(ov) if ov else None,
        obs_n=len(obs), obs_n_fog=n_fog_obs,
        obs_T_start=ot[0] if ot else None,
        obs_Td_start=od[0] if od else None,
        T_soil=(None if fin.get("T_soil") is None
                else float(fin["T_soil"]) - 273.15),
        regime=str(regime)[:40] if regime else None,
        hourly=hourly)

    rec.update(sample(_CAP.get("icon"), "icon"))
    rec.update(sample(fin, "prof"))

    cc = fin.get("cc_series") or []
    if cc:
        lo = [float(c[0]) for c in cc if c]
        rec["cf_start"] = lo[0] if lo else None
        rec["cf_mean"] = float(np.mean(lo)) if lo else None
        rec["cf_max"] = float(max(lo)) if lo else None

    # приземен вятър от началния профил
    if fin.get("u") is not None:
        u0 = float(np.asarray(fin["u"])[0])
        v0 = float(np.asarray(fin["v"])[0])
        rec["wind_start"] = float(np.hypot(u0, v0))
    return rec


paths = sorted(glob.glob(os.path.join(opt.cases_dir, "*.txt")))
if opt.airport:
    paths = [p for p in paths if opt.airport in os.path.basename(p)]
if opt.limit:
    paths = paths[:opt.limit]

done = {}
if os.path.exists(opt.out) and not opt.restart:
    try:
        done = {r["case"]: r for r in
                json.load(open(opt.out, encoding="utf-8"))["cases"]}
        print(f"[продължавам] {len(done)} случая вече записани")
    except (OSError, json.JSONDecodeError, KeyError):
        done = {}

errs = []
for k, path in enumerate(paths, 1):
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem in done:
        continue
    try:
        done[stem] = run_one(path)
        print(f"  [{k}/{len(paths)}] {stem:<28} {done[stem]['event']:<5} "
              f"minVIS={done[stem]['mod_min_vis']:>6.0f}", flush=True)
    except KeyboardInterrupt:
        print("\n[прекъснато] състоянието е запазено")
        break
    except Exception as e:
        errs.append((stem, f"{type(e).__name__}: {e}"))
        print(f"  [{k}/{len(paths)}] {stem:<28} ГРЕШКА {str(e)[:50]}",
              flush=True)
    if k % 10 == 0 or k == len(paths):
        json.dump({"cases": list(done.values())},
                  open(opt.out, "w", encoding="utf-8"),
                  ensure_ascii=False, default=float)

json.dump({"cases": list(done.values())},
          open(opt.out, "w", encoding="utf-8"),
          ensure_ascii=False, default=float)

# ── плоска таблица
rows = []
for r in done.values():
    flat = {k: v for k, v in r.items() if k != "hourly"}
    hh = r.get("hourly", [])
    early = [x for x in hh if 18.0 <= (x["hour"] + 24 if x["hour"] < 12 else
                                       x["hour"]) <= 22.0]
    if early:
        flat["rh_early_mean"] = float(np.mean([x["rh"] for x in early]))
        flat["rh_early_max"] = float(max(x["rh"] for x in early))
        flat["qv_early_mean"] = float(np.mean([x["qv"] for x in early]))
        flat["T_early_mean"] = float(np.mean([x["T"] for x in early]))
    if hh:
        flat["rh_night_max"] = float(max(x["rh"] for x in hh))
        flat["T_min"] = float(min(x["T"] for x in hh))
        flat["dT_night"] = float(min(x["T"] for x in hh) - hh[0]["T"])
    rows.append(flat)

keys = sorted({k for r in rows for k in r}) if rows else []
if rows:
    with open(opt.csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["case"]):
            w.writerow(r)

print(f"\n{'='*70}")
print(f"  записани : {len(done)} случая")
print(f"  грешки   : {len(errs)}")
print(f"  [JSON] {opt.out}")
print(f"  [CSV]  {opt.csv}   ({len(keys)} колони)")
print(f"{'='*70}")
for stem, e in errs[:10]:
    print(f"    {stem}: {e}")
