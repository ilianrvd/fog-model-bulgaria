# -*- coding: utf-8 -*-
"""
probe_kh.py — носещ член ли е Kh за мъглообразуването?
=======================================================
Пуска ИЗБРАНИ случаи с v8 физиката и изкуствено смалено Kh/Km, без
никаква друга промяна. Не пипа файлове, не запечатва нищо.

    python probe_kh.py                       # 12-те обърнати от Етап 2'
    python probe_kh.py --factors 1.0 0.5 0.3 0.1
    python probe_kh.py --cases LBGO_CFOG_2024-03-03 LBSF_CFOG_2024-10-13

Въпросът
--------
Етап 2′ обърна 7 CFOG случая от HIT в MISS. Две конкурентни обяснения:

  (А) Kh е НОСЕЩ член — завишената дифузия подава влага от по-високите
      нива към приземния слой и подпомага кондензацията. C2 уби
      подаването.
  (Б) Носеща е връзката U → H — прогностичният вятър пада от 0.67 на
      0.36 m/s, H пада с ~45 %, приземното охлаждане не стига.

Сондата разделя двете: тя смалява Kh, БЕЗ да пипа u, тоест H остава
непроменено. Ако мъглата пада → (А). Ако оцелява → (Б), и „петият
член" е преброен погрешно.

Мащабирането е върху ДВАТА клона (TKE и Louis пода), за да е Kh в
step() наистина умножено по фактора.
"""
import sys, os, io, json, argparse, contextlib
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 12-те обръщания от Етап 2′ (verify_2026-07-30_1234)
# 17-те случая от диагностичната кампания — за ръчката ch
CAMPAIGN_CASES = [
    "LBGO_CFOG_2024-03-03", "LBGO_CFOG_2024-11-16",
    "LBGO_CFOG_2025-02-01", "LBGO_CFOG_2025-03-04",
    "LBSF_CFOG_2024-10-13", "LBSF_CFOG_2024-10-21",
    "LBWN_CFOG_2025-01-30", "LBGO_CFOG_2024-12-30",
    "LBGO_CFOG_2024-02-17", "LBGO_CFOG_2025-01-31",
    "LBWN_CFOG_2024-11-17", "LBPD_CFOG_2024-12-30",
    "LBGO_CDRY_2024-10-20", "LBGO_CDRY_2025-03-05",
    "LBPD_CDRY_2024-12-31", "LBPD_CDRY_2025-01-17",
    "LBWN_CDRY_2026-07-21",
]

DEFAULT_CASES = [
    # HIT → MISS, CFOG
    "LBGO_CFOG_2024-03-03", "LBGO_CFOG_2024-11-16",
    "LBGO_CFOG_2025-02-01", "LBGO_CFOG_2025-03-04",
    "LBSF_CFOG_2024-10-13", "LBSF_CFOG_2024-10-21",
    "LBWN_CFOG_2025-01-30",
    # HIT → MISS, CLDY
    "LBPD_CLDY_2025-02-01", "LBSF_CLDY_2024-01-19", "LBSF_CLDY_2024-01-23",
    # CN → FA
    "LBSF_CDRY_2024-11-06", "LBWN_CLDY_2025-02-02",
    # контроли: минаха и при C2
    "LBGO_CFOG_2024-12-30", "LBGO_CDRY_2025-03-07",
]

ap = argparse.ArgumentParser()
ap.add_argument("--cases", nargs="*", default=None)
ap.add_argument("--knob", choices=["u", "kh", "ch", "g"], default="u",
                help="u = приземния вятър в SEB; kh = дифузията; "
                     "ch = обменния коефициент C_H_BULK (топлина И влага); "
                     "g = почвената проводимост LAMBDA_G")
ap.add_argument("--factors", nargs="*", type=float,
                default=[1.0, 0.7, 0.55, 0.4])
ap.add_argument("--cases-dir", default="cases")
opt = ap.parse_args()

# Диагностиката трябва да е ВКЛЮЧЕНА — сондата чете Tskin/Tair от SEB
# редовете за М2. Изходът се прихваща, така че не се вижда.
os.environ["SEB_DEBUG"] = "1"
import fog_model as fm
import verify_cases as vc
fm.SEB_DEBUG = True

_tke_orig   = fm.tke_step
_louis_orig = fm.louis_stability_function
_seb_orig   = fm.seb_step
SCALE = [1.0]


def _tke_scaled(*a, **kw):
    e, Km, Kh = _tke_orig(*a, **kw)
    return e, Km * SCALE[0], Kh * SCALE[0]


def _louis_scaled(*a, **kw):
    return _louis_orig(*a, **kw) * SCALE[0]


def _seb_scaled(*a, **kw):
    """Мащабира САМО приземния вятър, който влиза в H = ρ·cp·C_H·U·ΔT.
    Импулсът, TKE и Kh остават непроменени — изолира пътя U → H."""
    a = list(a)
    if len(a) > 7:
        a[6] = a[6] * SCALE[0]
        a[7] = a[7] * SCALE[0]
    return _seb_orig(*a, **kw)


_CH_ORIG = fm.C_H_BULK
_LG_ORIG = fm.LAMBDA_G

if opt.knob == "kh":
    fm.tke_step = _tke_scaled
    fm.louis_stability_function = _louis_scaled
elif opt.knob == "u":
    fm.seb_step = _seb_scaled
# ch: няма нужда от обвивка — C_H_BULK се чете като модулна глобална
# при всяко викане на seb_step, тоест присвояването действа веднага.
# ВАЖНО: скалира и H, и E_dew, защото са един и същ коефициент
# (редове 266 и 274). Това е физичното — C_H ≈ C_q е стандартът.


_PAIR_RE = None
def _re_pairs(txt):
    """(Tskin, Tair) двойки от SEB редовете, само нощни (sw≈0)."""
    global _PAIR_RE
    import re as _re
    if _PAIR_RE is None:
        _PAIR_RE = _re.compile(
            r"SEB\s+[\d.]+h\s+sw=\s*([-\d.]+).*?Tskin=\s*([-+\d.]+)"
            r"\s+Tair=\s*([-+\d.]+)")
    out = []
    for m in _PAIR_RE.finditer(txt):
        if float(m.group(1)) < 1.0:
            out.append((float(m.group(2)), float(m.group(3))))
    return out


def run_case(stem, factor):
    """Един случай при даден фактор. Връща (event, minVIS, MAE_T) или None."""
    path = os.path.join(opt.cases_dir, stem + ".txt")
    if not os.path.exists(path):
        return None
    SCALE[0] = factor
    if opt.knob == "ch":
        fm.C_H_BULK = _CH_ORIG
        fm.LAMBDA_G = _LG_ORIG * factor
    elif opt.knob == "g":
        fm.LAMBDA_G = _LG_ORIG * factor
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            icao, cat, ds, obs = vc.load_case_file(path)
            hist, _reg = vc.run_model(icao, ds, vc.START_HOUR, obs)
            ev = vc.evaluate(hist, obs, vc.START_HOUR, ds)
        prof = [r for r in hist if abs(r["hour_utc"] - round(r["hour_utc"])) < 0.01]
        ratio = None
        if len(prof) >= 2:
            qv0, qv1 = float(prof[0]["qv"][0]), float(prof[-1]["qv"][0])
            T0 = float(prof[0]["T_sfc"]); Tm = min(float(x["T_sfc"]) for x in prof)
            e0 = float(fm.sat_vapor_pressure(np.array([T0]))[0])
            em = float(fm.sat_vapor_pressure(np.array([Tm]))[0])
            dqs = 0.622*em/(1e5-em) - 0.622*e0/(1e5-e0)
            dqv = qv1 - qv0
            ratio = abs(dqv) / max(abs(dqs), 1e-9)
        dT_sa = None
        pairs = _re_pairs(buf.getvalue())
        if pairs:
            dT_sa = sum(a - b for a, b in pairs) / len(pairs)
        return (ev["event"],
                float(ev["mod_min_vis"]),
                None if ev["T"]["MAE"] is None else float(ev["T"]["MAE"]),
                ratio, dT_sa)
    except Exception as e:
        return ("ГРЕШКА", f"{type(e).__name__}: {e}")
    finally:
        SCALE[0] = 1.0
        fm.C_H_BULK = _CH_ORIG
        fm.LAMBDA_G = _LG_ORIG


cases = opt.cases if opt.cases else (
    CAMPAIGN_CASES if opt.knob == "ch" else DEFAULT_CASES)
print(f"\n{'='*88}")
KNOB_TXT = {"u":  "приземният вятър в SEB (U → H)",
            "kh": "Kh/Km в дифузията",
            "ch": "обменният коефициент C_H_BULK — топлина И влага",
            "g":  "почвената проводимост LAMBDA_G (→ G)"}[opt.knob]
print(f"  СОНДА — {len(cases)} случая × {len(opt.factors)} фактора")
print(f"  v8 физика. Мащабира се: {KNOB_TXT}")
if opt.knob == "u":
    print(f"  C2 свали U от 0.67 на 0.36 m/s, тоест фактор ≈ 0.55.")
if opt.knob == "ch":
    print(f"  Сега C_H·U = 0.0008 m/s; нужни са ~0.005–0.02, тоест 6–25×.")
    print(f"  Колоната след VIS е |Δqv роса| / |Δqsat охл.| — М3 иска < 1.")
if opt.knob == "g":
    print(f"  Сега LAMBDA_G/D_SOIL_G = 10 W/m²/K, G медиана +21 W/m².")
    print(f"  Колоната след VIS е T_skin − T_air — М2 иска −3…−6 K.")
    print(f"  Медианата сега е −0.44 K: кожата е закотвена за почвата.")
print(f"{'='*88}")

_kn = {"u": "U ", "kh": "Kh", "ch": "C_H", "g": "λG"}[opt.knob]
hdr = f"  {'случай':<26}"
for f in opt.factors:
    hdr += f" | {_kn}×{f:<4.1f}          "
print(hdr)
print("  " + "-" * 84)

rows, errors = {}, {}
for stem in cases:
    line = f"  {stem:<26}"
    rows[stem] = {}
    for f in opt.factors:
        r = run_case(stem, f)
        if r is None:
            line += f" | {'няма файл':<16}"
            continue
        if r[0] == "ГРЕШКА":
            errors.setdefault(stem, r[1])
            line += f" | {'ГРЕШКА':<16}"
            continue
        ev, vmin, mae, ratio, dts = r
        rows[stem][f] = (ev, vmin, mae, ratio, dts)
        if opt.knob == "g" and dts is not None:
            extra = f"{dts:+.2f}"
        elif opt.knob == "ch" and ratio is not None:
            extra = f"{ratio:.2f}"
        else:
            extra = f"{mae:.1f}" if mae is not None else ""
        line += f" | {ev:<5} {vmin:>5.0f}m {extra:>5}"
    print(line, flush=True)

if errors:
    print(f"\n  ГРЕШКИ ({len(errors)} случая) — първите съобщения:")
    for k, v in list(errors.items())[:5]:
        print(f"    {k}: {v}")

# ── Присъда
print("\n" + "=" * 88)
print("  РАЗБОР")
print("=" * 88)
fog_cases = [c for c in rows if "_CFOG_" in c or "_CLDY_" in c]
base_f = opt.factors[0]
low_f = opt.factors[-1]
lost, kept = [], []
for c in fog_cases:
    if base_f not in rows[c] or low_f not in rows[c]:
        continue
    e0 = rows[c][base_f][0]
    e1 = rows[c][low_f][0]
    if e0 == "HIT" and e1 != "HIT":
        lost.append(c)
    elif e0 == "HIT":
        kept.append(c)

kn = {"u": "U", "kh": "Kh", "ch": "C_H", "g": "λG"}[opt.knob]
print(f"  При {kn}×{low_f}:  мъглата пада в {len(lost)} случая, "
      f"оцелява в {len(kept)}")
if lost:
    print(f"    падат : {', '.join(lost)}")
if kept:
    print(f"    оцеляват: {', '.join(kept)}")
print()
if opt.knob == "u":
    if len(lost) >= 4:
        print("  → потвърждава (Б): обръщанията при C2 идват от U → H.")
        print("    По-слабият приземен вятър намалява H и приземното")
        print("    охлаждане не стига. Следствие: C2 е поправим ЗАЕДНО с")
        print("    коректен приземен обмен — Етап 3 в скицирания вид")
        print("    остава смислен, а 'петият член' пада като хипотеза.")
    else:
        print("  → (Б) НЕ се потвърждава: смаляването на U само по себе си")
        print("    не обръща случаите. Значи при C2 действа комбинация от")
        print("    ефекти и трябва отделна сонда за всеки.")
elif opt.knob == "g":
    print("  М2 — T_skin − T_air (мишена −3…−6 K при тиха ясна нощ):")
    for f in opt.factors:
        dd = [rows[c][f][4] for c in rows
              if f in rows[c] and rows[c][f][4] is not None]
        nf = sum(1 for c in rows if f in rows[c] and rows[c][f][0] == "HIT")
        if dd:
            med = sorted(dd)[len(dd)//2]
            print(f"    LAMBDA_G×{f:<5.2f}  медиана ΔT = {med:>+6.2f} K"
                  f"   |  HIT случаи: {nf:>2}")
    print()
    print("    Ако ΔT се движи към −3…−6 при по-малко LAMBDA_G,")
    print("    почвата е членът за М2 и влиза в групата.")
elif opt.knob == "ch":
    print("  М3 — балансът роса/охлаждане при усилен обмен:")
    for f in opt.factors:
        rr = [rows[c][f][3] for c in rows
              if f in rows[c] and rows[c][f][3] is not None]
        hh = [rows[c][f][3] for c in rows
              if f in rows[c] and rows[c][f][0] == "HIT"
              and rows[c][f][3] is not None]
        if not rr:
            continue
        med = sorted(rr)[len(rr)//2]
        medh = sorted(hh)[len(hh)//2] if hh else float("nan")
        n_fog = sum(1 for c in rows if f in rows[c] and rows[c][f][0] == "HIT")
        print(f"    C_H×{f:<4.1f}  медиана всички {med:>5.2f}  "
              f"при HIT {medh:>5.2f}  |  HIT случаи: {n_fog:>2}")
    print()
    print("    М3 иска отношението < 1 в HIT популацията.")
    print("    Ако расте над 1 → лечението е в кондензацията")
    print("    (надситеност/хистерезис), НЕ в разцепване на обмена.")
else:
    print("  → Забележка: синтетично измерено, по-малко Kh дава ПОВЕЧЕ")
    print("    мъгла (VIS 1080 → 864 при Kh×0.01). Ако това се потвърди")
    print("    и тук, Kh не е носещ член в предполаганата посока.")
print("=" * 88)
