# -*- coding: utf-8 -*-
"""
probe_discriminate.py — разделя ли нещо CFOG от FA нощите?
===========================================================
Решаващият въпрос преди спецификацията на Етап 3.

    python probe_discriminate.py > probe_disc.txt 2>&1

Защо
----
Сондата с C_H показа, че усилването на обмена поправя част от FA, но
убива част от мъглата. Спасение има само ако формулата РАЗЛИЧАВА двете
популации — висок обмен при FA нощите, запазен при CFOG нощите.

Но и двете са тих, ясен, устойчив режим. Ако Ri (или каквото и да е
друго) не ги разделя, никаква C_H(Ri) формула не може да поправи FA,
без да убие мъглата, и Етап 3 в този вид няма решение.

Затова скриптът НЕ проверява само Ri. Той събира десетина кандидата и
за всеки смята колко добре разделя двете популации.

Важно: разделянето се търси в РАННАТА нощ (18–22 UTC), преди мъглата да
се е образувала. След това всичко се променя от самата мъгла (eps_a=1)
и разделянето става тавтология.
"""
import sys, os, io, re, json, argparse, contextlib
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["SEB_DEBUG"] = "1"
import fog_model as fm
import verify_cases as vc
fm.SEB_DEBUG = True

FOG_CASES = [
    "LBGO_CFOG_2024-03-03", "LBGO_CFOG_2024-11-16",
    "LBGO_CFOG_2025-02-01", "LBGO_CFOG_2025-03-04",
    "LBSF_CFOG_2024-10-13", "LBSF_CFOG_2024-10-21",
    "LBWN_CFOG_2025-01-30", "LBGO_CFOG_2024-12-30",
    "LBGO_CFOG_2024-02-17", "LBGO_CFOG_2025-01-31",
    "LBWN_CFOG_2024-11-17", "LBPD_CFOG_2024-12-30",
]
FA_CASES = [
    "LBGO_CDRY_2024-10-20", "LBGO_CDRY_2025-03-05",
    "LBPD_CDRY_2024-12-31", "LBPD_CDRY_2025-01-17",
    "LBWN_CDRY_2026-07-21", "LBGO_CDRY_2025-03-07",
    "LBSF_CDRY_2024-11-06",
]

ap = argparse.ArgumentParser()
ap.add_argument("--cases-dir", default="cases")
ap.add_argument("--early", nargs=2, type=float, default=[18.0, 22.0],
                help="ранен прозорец за разделянето [UTC]")
ap.add_argument("--dump", default="probe_disc.json")
opt = ap.parse_args()

# ── прихващане на Ri и S ────────────────────────────────────
_bulk_orig = fm.bulk_richardson_and_shear
RI_LOG = []


def _bulk_logged(theta_v, u, v, z):
    Ri, S = _bulk_orig(theta_v, u, v, z)
    RI_LOG.append((float(Ri[1]), float(Ri[3]), float(S[1])))
    return Ri, S


fm.bulk_richardson_and_shear = _bulk_logged

SEB_RE = re.compile(
    r"SEB\s+([\d.]+)h\s+sw=\s*([-\d.]+)\s+Rnet=\s*([-+\d.]+)\s+negH=\s*([-+\d.]+)"
    r"\s+G=\s*([-+\d.]+)\s+LE=\s*([-+\d.]+).*?Tskin=\s*([-+\d.]+)"
    r"\s+Tair=\s*([-+\d.]+)\s+Tsoil=\s*([-+\d.]+).*?U=([\d.]+)"
    r"\s+cf=([\d.]+)\s+LWP=([\d.]+)kg/m2")


def run(stem, label):
    path = os.path.join(opt.cases_dir, stem + ".txt")
    if not os.path.exists(path):
        return None, "няма файл"
    RI_LOG.clear()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            icao, cat, ds, obs = vc.load_case_file(path)
            hist, _ = vc.run_model(icao, ds, vc.START_HOUR, obs)
            ev = vc.evaluate(hist, obs, vc.START_HOUR, ds)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    txt = buf.getvalue()
    seb = {}
    for m in SEB_RE.finditer(txt):
        seb[round(float(m.group(1)), 1)] = dict(
            sw=float(m.group(2)), rnet=float(m.group(3)),
            H=-float(m.group(4)), G=float(m.group(5)), LE=float(m.group(6)),
            Tskin=float(m.group(7)), Tair=float(m.group(8)),
            Tsoil=float(m.group(9)), U=float(m.group(10)),
            cf=float(m.group(11)), lwp=float(m.group(12)))

    # Ri по часове: един запис на стъпка, dt=60 s
    ri_h = {}
    for k, (ri1, ri3, s1) in enumerate(RI_LOG):
        h = round((vc.START_HOUR + k / 60.0) % 24, 1)
        ri_h.setdefault(h, []).append((ri1, ri3, s1))

    rec = []
    for r in hist:
        h = round(r["hour_utc"], 1)
        if abs(h - round(h)) > 0.01:
            continue
        e = seb.get(round(h, 1))
        ri = ri_h.get(round(h, 1))
        rec.append(dict(
            hour=h,
            T=float(r["T_sfc"]) - 273.15, rh=float(r["rh_sfc"]),
            qv=float(r["qv"][0]) * 1000, ql=float(r["ql_sfc"]) * 1000,
            vis=float(r["vis_sfc"]),
            Ri1=float(np.median([x[0] for x in ri])) if ri else None,
            Ri3=float(np.median([x[1] for x in ri])) if ri else None,
            S1=float(np.median([x[2] for x in ri])) if ri else None,
            **({} if not e else
               dict(U=e["U"], H=e["H"], G=e["G"], cf=e["cf"],
                    dTsa=e["Tskin"] - e["Tair"], rnet=e["rnet"],
                    lwp=e["lwp"], sw=e["sw"]))))

    # статични признаци от METAR-ите
    spread = [o["T"] - o["Td"] for o in obs
              if o.get("T") is not None and o.get("Td") is not None]
    return dict(case=stem, label=label, icao=icao, event=ev["event"],
                min_vis=float(ev["mod_min_vis"]),
                spread_min=min(spread) if spread else None,
                rec=rec), None


res, errs = [], []
for stem in FOG_CASES:
    r, e = run(stem, "FOG")
    (res.append(r) if r else errs.append((stem, e)))
    print(f"  {'готов ' if r else 'ГРЕШКА'} {stem:<26}"
          f"{'' if not r else ' ' + r['event']}", flush=True)
for stem in FA_CASES:
    r, e = run(stem, "FA")
    (res.append(r) if r else errs.append((stem, e)))
    print(f"  {'готов ' if r else 'ГРЕШКА'} {stem:<26}"
          f"{'' if not r else ' ' + r['event']}", flush=True)

if not res:
    sys.exit("Нула успешни случая.")

h0, h1 = opt.early


def early_recs(r):
    """Часове в [h0, h1] по РЕАЛНО време от старта, не по UTC по модул 24."""
    out = []
    for x in r["rec"]:
        h = x["hour"]
        hh = h + 24.0 if h < vc.START_HOUR - 0.5 else h   # 0h → 24h
        if h0 <= hh <= h1:
            out.append(x)
    return out


FIELDS = [("Ri1", "Ri на ниво 1"), ("Ri3", "Ri на ниво 3"),
          ("S1", "срез² на ниво 1"), ("U", "приземен вятър"),
          ("dTsa", "T_skin − T_air"), ("H", "сензибилен поток"),
          ("G", "почвен поток"), ("rnet", "нетна радиация"),
          ("cf", "облачност"), ("rh", "RH приземно"),
          ("qv", "qv приземно"), ("T", "T приземно")]

print(f"\n{'='*96}")
print(f"  РАННА НОЩ {h0:.0f}–{h1:.0f} UTC — средни по случай")
print(f"{'='*96}")
hdr = f"  {'случай':<26} {'кл':<4}"
for k, _ in FIELDS[:6]:
    hdr += f" {k:>9}"
print(hdr)
vals = {k: {"FOG": [], "FA": []} for k, _ in FIELDS}
for r in res:
    er = early_recs(r)
    if not er:
        continue
    line = f"  {r['case']:<26} {r['label']:<4}"
    for k, _ in FIELDS:
        v = [x[k] for x in er if x.get(k) is not None]
        if v:
            mv = float(np.mean(v))
            vals[k][r["label"]].append(mv)
            if k in [f[0] for f in FIELDS[:6]]:
                line += f" {mv:>9.3f}"
        elif k in [f[0] for f in FIELDS[:6]]:
            line += f" {'—':>9}"
    print(line)

# спред от METAR
for r in res:
    if r["spread_min"] is not None:
        vals.setdefault("spread", {"FOG": [], "FA": []})
        vals["spread"][r["label"]].append(r["spread_min"])

print(f"\n{'='*96}")
print("  РАЗДЕЛИТЕЛНА СПОСОБНОСТ  (най-добър праг, точност)")
print(f"{'='*96}")
print(f"  {'признак':<22} {'FOG медиана':>12} {'FA медиана':>11} "
      f"{'праг':>9} {'точност':>9}")


def best_split(A, B):
    if not A or not B:
        return None
    best = (-1.0, None, None)
    for t in sorted(set(A + B)):
        for sign in (+1, -1):
            acc = (sum(1 for x in A if sign * x >= sign * t) +
                   sum(1 for x in B if sign * x < sign * t)) / (len(A) + len(B))
            if acc > best[0]:
                best = (acc, t, sign)
    return best


ranked = []
for k, name in FIELDS + [("spread", "мин. T−Td от METAR")]:
    if k not in vals:
        continue
    A, B = vals[k]["FOG"], vals[k]["FA"]
    r = best_split(A, B)
    if not r:
        continue
    acc, t, sign = r
    ranked.append((acc, k, name, np.median(A), np.median(B), t))
ranked.sort(reverse=True)
for acc, k, name, ma, mb, t in ranked:
    print(f"  {name:<22} {ma:>12.3f} {mb:>11.3f} {t:>9.3f} {acc:>8.0%}")

print(f"\n{'='*96}")
best_acc, best_k, best_name = ranked[0][0], ranked[0][1], ranked[0][2]
print(f"  Най-добър разделител: {best_name}  ({best_acc:.0%})")
ri_acc = next((a for a, k, *_ in ranked if k == "Ri1"), 0.0)
print(f"  Ri на ниво 1: {ri_acc:.0%}")
print()
if ri_acc >= 0.85:
    print("  → Ri РАЗДЕЛЯ двете популации. C_H(Ri) формула има основа;")
    print("    Етап 3 може да поправи FA, без да убие мъглата.")
elif best_acc >= 0.85:
    print(f"  → Ri не разделя, но '{best_name}' го прави. Формулата трябва")
    print(f"    да ключва по него, не по Ri. Проверете дали е физически")
    print(f"    оправдано като аргумент на обменен коефициент.")
else:
    print("  → НИЩО не разделя двете популации над 85 %.")
    print("    CFOG и FA нощите са един и същи режим за модела.")
    print("    Никаква C_H(x) формула не може да поправи FA, без да")
    print("    убие мъглата. Етап 3 в този вид няма решение —")
    print("    разграничителят трябва да се търси другаде (микрофизика,")
    print("    начални условия, или информация, която моделът няма).")
print(f"{'='*96}")

json.dump({"cases": res}, open(opt.dump, "w", encoding="utf-8"),
          ensure_ascii=False, default=float)
print(f"\n  [JSON] {opt.dump}")
if errs:
    for k, v in errs:
        print(f"  ГРЕШКА {k}: {v}")
