# -*- coding: utf-8 -*-
"""
campaign_seb.py — диагностична кампания за приземния обмен
===========================================================
Събира бюджета на приземната енергия и близостта до насищане върху
избрани случаи, за да се изберат формулата за C_H и физическите мишени
ПРЕДИ да се пише код.

    python campaign_seb.py > campaign.txt 2>&1
    python campaign_seb.py --cases LBGO_CFOG_2024-03-03 ...

Не пипа файлове, не запечатва нищо. v8 физика, без промени.

Двата въпроса на кампанията
---------------------------
А. Доминира ли G навсякъде, или само при топла почва?
   При LBGO 2024-12-30 почвата покрива 47 от 53 W/m² радиационна загуба.
   Ако е универсално → LAMBDA_G/D_SOIL_G е съзаподозрян. Ако е само при
   топла почва → C_H е сам.

Б. Колко близо до насищане стига въздухът и колко влага сваля росата?
   Етап 2 показа, че при жива архитектура росата надбягва кондензацията.
   Стесненият обхват на Етап 3 задържа Дирихле и DZ_EFF, тоест росата
   минава по стария път — но C_H промяната усилва и E_dew, защото е
   същият коефициент.

Мишена за H, от литературата: −10…−30 W/m² в тиха ясна нощ.
Сега моделът дава +2.8…+5.0 (знакът в SEB реда е negH = −H).
"""
import sys, os, io, re, json, argparse, contextlib
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_CASES = [
    # седемте обърнати от C2 — граничните
    "LBGO_CFOG_2024-03-03", "LBGO_CFOG_2024-11-16",
    "LBGO_CFOG_2025-02-01", "LBGO_CFOG_2025-03-04",
    "LBSF_CFOG_2024-10-13", "LBSF_CFOG_2024-10-21",
    "LBWN_CFOG_2025-01-30",
    # устойчиви CFOG за контраст
    "LBGO_CFOG_2024-12-30", "LBGO_CFOG_2024-02-17",
    "LBGO_CFOG_2025-01-31", "LBWN_CFOG_2024-11-17",
    "LBPD_CFOG_2024-12-30",
    # FA популацията — където искаме промяна
    "LBGO_CDRY_2024-10-20", "LBGO_CDRY_2025-03-05",
    "LBPD_CDRY_2024-12-31", "LBPD_CDRY_2025-01-17",
    "LBWN_CDRY_2026-07-21",
]

ap = argparse.ArgumentParser()
ap.add_argument("--cases", nargs="*", default=None)
ap.add_argument("--cases-dir", default="cases")
ap.add_argument("--dump", default="campaign_seb.json")
opt = ap.parse_args()

os.environ["SEB_DEBUG"] = "1"
import fog_model as fm
import verify_cases as vc
fm.SEB_DEBUG = True

SEB_RE = re.compile(
    r"SEB\s+([\d.]+)h\s+sw=\s*([-\d.]+)\s+Rnet=\s*([-+\d.]+)\s+negH=\s*([-+\d.]+)"
    r"\s+G=\s*([-+\d.]+)\s+LE=\s*([-+\d.]+).*?Tskin=\s*([-+\d.]+)"
    r"\s+Tair=\s*([-+\d.]+)\s+Tsoil=\s*([-+\d.]+).*?U=([\d.]+)"
    r"\s+cf=([\d.]+)\s+LWP=([\d.]+)kg/m2\s+is_fog=(\w+)")

Lv = 2.5e6
CP = 1005.0


def qsat(T_K, p_Pa):
    es = fm.sat_vapor_pressure(np.asarray([T_K], dtype=float))[0]
    return 0.622 * es / max(p_Pa - es, 1.0)


def run(stem):
    path = os.path.join(opt.cases_dir, stem + ".txt")
    if not os.path.exists(path):
        return None, f"няма файл {path}"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            icao, cat, ds, obs = vc.load_case_file(path)
            hist, _ = vc.run_model(icao, ds, vc.START_HOUR, obs)
            ev = vc.evaluate(hist, obs, vc.START_HOUR, ds)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    seb = []
    for m in SEB_RE.finditer(buf.getvalue()):
        g = m.groups()
        seb.append(dict(hour=float(g[0]), sw=float(g[1]), rnet=float(g[2]),
                        negH=float(g[3]), G=float(g[4]), LE=float(g[5]),
                        Tskin=float(g[6]), Tair=float(g[7]),
                        Tsoil=float(g[8]), U=float(g[9]), cf=float(g[10]),
                        lwp=float(g[11]), is_fog=(g[12] == "True")))
    # състояние по часове от history
    prof = []
    for r in hist:
        if abs(r["hour_utc"] - round(r["hour_utc"])) > 0.01:
            continue
        prof.append(dict(hour=float(r["hour_utc"]),
                         T=float(r["T_sfc"]), rh=float(r["rh_sfc"]),
                         qv=float(r["qv"][0]), ql=float(r["ql_sfc"]),
                         vis=float(r["vis_sfc"])))
    return dict(case=stem, icao=icao, cat=cat, date=ds,
                event=ev["event"], min_vis=float(ev["mod_min_vis"]),
                mae_T=ev["T"]["MAE"], seb=seb, prof=prof), None


cases = opt.cases if opt.cases else DEFAULT_CASES
res, errs = [], []
for stem in cases:
    r, e = run(stem)
    if r is None:
        errs.append((stem, e))
        print(f"  ГРЕШКА {stem}: {e}", flush=True)
    else:
        res.append(r)
        print(f"  готов  {stem:<26} {r['event']:<5} minVIS={r['min_vis']:>6.0f}",
              flush=True)

if not res:
    sys.exit("\nНула успешни случая — кампанията не може да продължи.")


def night(r):
    """Нощните часове (без слънце) от SEB записите."""
    return [s for s in r["seb"] if s["sw"] < 1.0]


def pct(v, q):
    v = sorted(v)
    if not v:
        return float("nan")
    return v[max(0, min(len(v) - 1, int(round(q * (len(v) - 1)))))]


print(f"\n{'='*94}")
print(f"  ПО СЛУЧАИ  ({len(res)} случая, само нощни часове)")
print(f"{'='*94}")
print(f"  {'случай':<26} {'ев':<5} {'H':>16} {'G':>14} {'ΔT ск-възд':>11} "
      f"{'U':>10} {'G/|Rnet|':>9}")
print(f"  {'':<26} {'':<5} {'ср / мин':>16} {'ср / макс':>14} {'ср':>11} {'ср':>10}")
print("  " + "-" * 90)
for r in res:
    n = night(r)
    if not n:
        continue
    H = [-s["negH"] for s in n]            # negH = −H → H = −negH
    G = [s["G"] for s in n]
    dT = [s["Tskin"] - s["Tair"] for s in n]
    U = [s["U"] for s in n]
    gr = [abs(s["G"]) / max(abs(s["rnet"]), 1e-6) for s in n]
    print(f"  {r['case']:<26} {r['event']:<5} "
          f"{np.mean(H):>7.1f} /{min(H):>7.1f} "
          f"{np.mean(G):>6.1f} /{max(G):>6.1f} "
          f"{np.mean(dT):>11.2f} {np.mean(U):>10.2f} {np.mean(gr):>9.2f}")

# ── Въпрос А
allH  = [-s["negH"] for r in res for s in night(r)]
allG  = [s["G"] for r in res for s in night(r)]
allR  = [s["rnet"] for r in res for s in night(r)]
allU  = [s["U"] for r in res for s in night(r)]
alldT = [s["Tskin"] - s["Tair"] for r in res for s in night(r)]
gratio = [abs(g) / max(abs(rn), 1e-6) for g, rn in zip(allG, allR)]
warm = [abs(g) / max(abs(rn), 1e-6) for r in res for s in night(r)
        for g, rn in [(s["G"], s["rnet"])] if s["Tsoil"] > s["Tair"]]
cold = [abs(g) / max(abs(rn), 1e-6) for r in res for s in night(r)
        for g, rn in [(s["G"], s["rnet"])] if s["Tsoil"] <= s["Tair"]]

print(f"\n{'='*94}")
print("  ВЪПРОС А — доминира ли G?")
print(f"{'='*94}")
print(f"  {'величина':<22} {'p10':>9} {'p50':>9} {'p90':>9}   мишена / бележка")
print(f"  {'H [W/m²]':<22} {pct(allH,.1):>9.1f} {pct(allH,.5):>9.1f} "
      f"{pct(allH,.9):>9.1f}   мишена −10…−30")
print(f"  {'G [W/m²]':<22} {pct(allG,.1):>9.1f} {pct(allG,.5):>9.1f} "
      f"{pct(allG,.9):>9.1f}")
print(f"  {'|G|/|Rnet|':<22} {pct(gratio,.1):>9.2f} {pct(gratio,.5):>9.2f} "
      f"{pct(gratio,.9):>9.2f}   >0.7 = почвата покрива загубата")
print(f"  {'T_skin − T_air [K]':<22} {pct(alldT,.1):>9.2f} {pct(alldT,.5):>9.2f} "
      f"{pct(alldT,.9):>9.2f}   реално −3…−6 в тиха ясна нощ")
print(f"  {'U [m/s]':<22} {pct(allU,.1):>9.2f} {pct(allU,.5):>9.2f} "
      f"{pct(allU,.9):>9.2f}")
ce = [fm.C_H_BULK * u for u in allU]
print(f"  {'C_H·U [m/s]':<22} {pct(ce,.1):>9.5f} {pct(ce,.5):>9.5f} "
      f"{pct(ce,.9):>9.5f}   нужни ~0.005–0.02")
print(f"\n  |G|/|Rnet| при ТОПЛА почва (n={len(warm)}): медиана "
      f"{pct(warm,.5) if warm else float('nan'):.2f}")
print(f"  |G|/|Rnet| при СТУДЕНА почва (n={len(cold)}): медиана "
      f"{pct(cold,.5) if cold else float('nan'):.2f}")
if warm and cold and pct(warm, .5) > 1.5 * pct(cold, .5):
    print("  → G доминира главно при ТОПЛА почва. C_H остава сам заподозрян.")
elif warm and cold:
    print("  → G доминира и в двата случая. LAMBDA_G/D_SOIL_G влиза в групата.")

# ── Въпрос Б
print(f"\n{'='*94}")
print("  ВЪПРОС Б — насищане и роса")
print(f"{'='*94}")
print(f"  {'случай':<26} {'RH макс':>8} {'qv нач':>8} {'qv кр.':>8} "
      f"{'Δqv роса':>9} {'Δqsat охл.':>11}   изход")
rows_b = []
for r in res:
    p = r["prof"]
    if len(p) < 2:
        continue
    rh_max = max(x["rh"] for x in p) * 100.0
    qv0, qv1 = p[0]["qv"] * 1000, p[-1]["qv"] * 1000
    qs0 = qsat(p[0]["T"], 1e5) * 1000
    qs1 = qsat(min(x["T"] for x in p), 1e5) * 1000
    dq_dew = qv1 - qv0
    dq_cool = qs1 - qs0
    rows_b.append((r["case"], rh_max, qv0, qv1, dq_dew, dq_cool, r["event"]))
    print(f"  {r['case']:<26} {rh_max:>7.1f}% {qv0:>8.2f} {qv1:>8.2f} "
          f"{dq_dew:>+9.2f} {dq_cool:>+11.2f}   {r['event']}")

if rows_b:
    ratio = [abs(d) / max(abs(c), 1e-6) for _, _, _, _, d, c, _ in rows_b]
    print(f"\n  |Δqv роса| / |Δqsat охлаждане|:  p10={pct(ratio,.1):.2f}  "
          f"p50={pct(ratio,.5):.2f}  p90={pct(ratio,.9):.2f}")
    print("  <1 = охлаждането изпреварва росата (мъгла възможна)")
    print("  >1 = росата изсушава по-бързо (Етап 2 падна тук)")
    fog_cases = [x for x in rows_b if x[6] == "HIT"]
    if fog_cases:
        rr = [abs(d) / max(abs(c), 1e-6) for _, _, _, _, d, c, _ in fog_cases]
        print(f"  само при HIT: медиана {pct(rr,.5):.2f}  (n={len(fog_cases)})")

json.dump({"cases": [{k: v for k, v in r.items()} for r in res]},
          open(opt.dump, "w", encoding="utf-8"), ensure_ascii=False,
          default=float)
print(f"\n  [JSON] {opt.dump}")
if errs:
    print(f"  ГРЕШКИ: {len(errs)}")
    for k, v in errs:
        print(f"    {k}: {v}")
