"""
dryrun_regime_hourly.py
=======================
Симулира ПОЧАСОВИЯ режимен автомат за континенталните летища (LBSF,
LBPD, LBGO), с праг по средния вятър в прозореца 18–06 UTC.

Мотив: сухият анализ на СТАТИЧЕН праг (dryrun_wind_criteria.py) даде
отрицателен знак при всяка стойност. Но `LBSF_CLDY_2024-01-23` показа,
че средният нощен вятър е грубата метрика: там средното е 4.7 kt, а в
часа на образуването е 3 kt. Почасовият автомат може да улови точно
това — влиза в dynamic при вятър и излиза, когато утихне.

Възпроизвежда механизма от verify_cases.run_model:
  - двоен критерий: влизане при V >= thr, излизане при V < thr
  - буфер pending_count >= 2 (две последователни несъгласия)
  - изгревът се пропуска (там режимът се сменя по друга причина)

НУЛА пускания на модела. Чете diagnostic_summary.json.

ВАЖНО: скриптът НЕ предсказва новия CSI. Той брои:
  - колко превключвания на случай (осцилира ли автоматът)
  - в кой режим е случаят в ЧАСА НА ОБРАЗУВАНЕТО на мъглата
  - кои FA/HIT попадат в dynamic в критичните часове

Употреба:
  python dryrun_regime_hourly.py
  python dryrun_regime_hourly.py --thresholds 2.5 3 3.5 4 5
  python dryrun_regime_hourly.py --source icon
  python dryrun_regime_hourly.py --detail 3.0
"""

import argparse
import json
import statistics
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


CONTINENTAL = ("LBSF", "LBPD", "LBGO")

# Измерен байъс ICON−METAR по летище (compare_wind_sources.py, 12.08.2026),
# прозорец 18–06 UTC, стара база 288 случая:
#   LBSF -1.33 kt   LBPD -1.12 kt   LBGO +0.44 kt
# ICON системно ПОДЦЕНЯВА вятъра в LBSF/LBPD (котловина) и леко го
# НАДЦЕНЯВА в LBGO. Прагът, калибриран по METAR, трябва да се измести
# със същия знак, за да хване същата физическа скорост.
ICON_BIAS_KT = {"LBSF": -1.33, "LBPD": -1.12, "LBGO": +0.44}

# Прозорецът, по който се съди: 18–06 UTC (по искане на Илиан).
# Изгревът е извън него — там режимът се сменя по слънчева причина.
WIN_START, WIN_END = 18, 6


def in_window(h):
    return h >= WIN_START or h <= WIN_END


def hour_key(h):
    """Подрежда 18,19..23,0..6 в монотонна редица."""
    return h - WIN_START if h >= WIN_START else h + (24 - WIN_START)


# ──────────────────────────────────────────────────────────────
# Вятър по час
# ──────────────────────────────────────────────────────────────

def hourly_wind(rec, source="metar"):
    """{час: вятър в kt} за прозореца. При няколко METAR в час — средно."""
    key = "wind_metar" if source == "metar" else "wind_icon"
    buckets = {}
    for w in rec.get(key, []):
        h = w.get("hour_utc")
        v = w.get("wind_kt")
        if h is None or v is None:
            continue
        h = int(h)
        if not in_window(h):
            continue
        buckets.setdefault(h, []).append(float(v))
    return {h: statistics.mean(vs) for h, vs in buckets.items()}


# ──────────────────────────────────────────────────────────────
# Автоматът
# ──────────────────────────────────────────────────────────────

def simulate(winds, thr, start_regime, buffer_n=2):
    """
    Връща (log, dominant, n_switches):
      log        {час: режим} след всяка стъпка
      dominant   режимът с най-много часове
      n_switches брой реални превключвания

    Двоен критерий, както в кода:
      radiative → dynamic : V >= thr
      dynamic → radiative : V <  thr
    Буфер: `buffer_n` последователни несъгласия преди смяна.
    """
    cur = start_regime
    pending = None
    pend_n = 0
    log = {}
    switches = 0

    for h in sorted(winds, key=hour_key):
        v = winds[h]
        cand = "dynamic" if v >= thr else "radiative"
        if cand != cur:
            pend_n = pend_n + 1 if cand == pending else 1
            pending = cand
            if pend_n >= buffer_n:
                cur = cand
                switches += 1
                pending, pend_n = None, 0
        else:
            pending, pend_n = None, 0
        log[h] = cur

    if not log:
        return {}, start_regime, 0
    dom = Counter(log.values()).most_common(1)[0][0]
    return log, dom, switches


def fog_hours(rec):
    """
    Часовете, в които МОДЕЛЪТ е правил мъгла, не се знаят от
    diagnostic_summary. Приближение: часовете, в които НАБЛЮДЕНИЕТО е
    под 2000 m, не са налични директно; ползваме vis_min_mod като белег,
    че мъглата изобщо е имало, и приемаме, че критичният час е този с
    минимален вятър — там охлаждането е най-силно.

    Връща (критичен_час, вятър_там) или (None, None).
    """
    return None, None


# ──────────────────────────────────────────────────────────────
# Отчет
# ──────────────────────────────────────────────────────────────

def run_threshold(rows, thr, source, buffer_n):
    """
    thr: число (общ праг за всички) ИЛИ dict {icao: праг}.
    При source='icon' и подаден thr_metar_equiv=None, тук се очаква
    прагът вече да е в ICON-мащаб — извикващият код прави корекцията.
    """
    out = []
    for r in rows:
        winds = r["_w"]
        if not winds:
            continue
        this_thr = thr[r["icao"]] if isinstance(thr, dict) else thr

        h0 = min(winds, key=hour_key)
        start = "dynamic" if winds[h0] >= this_thr else "radiative"
        log, dom, sw = simulate(winds, this_thr, start, buffer_n)

        h_calm = min(winds, key=lambda h: winds[h]) if winds else None
        reg_calm = log.get(h_calm) if h_calm is not None else None

        out.append({
            "rec": r, "log": log, "dominant": dom, "switches": sw,
            "h_calm": h_calm, "v_calm": winds.get(h_calm),
            "regime_at_calm": reg_calm, "thr_used": this_thr,
            "n_dyn": sum(1 for v in log.values() if v == "dynamic"),
            "n_rad": sum(1 for v in log.values() if v == "radiative"),
        })
    return out


def icon_equiv_thresholds(thr_metar, icaos):
    """Превежда METAR-калибриран праг в ICON-скала по измерения байъс."""
    return {icao: thr_metar + ICON_BIAS_KT.get(icao, 0.0) for icao in icaos}


def print_table(rows, thresholds, source, buffer_n, per_airport=False):
    base = Counter(r.get("event") for r in rows)
    b_h, b_m, b_f = base["HIT"], base["MISS"], base["FA"]
    b_csi = b_h / max(b_h + b_m + b_f, 1)
    print(f"\n  База: HIT={b_h} MISS={b_m} FA={b_f} CN={base['CN']}   "
          f"CSI={b_csi:.3f}   ({len(rows)} случая)")

    label = "METAR-екв. праг" if per_airport else "праг"
    print(f"\n  {label:>16} │ {'ср. прев.':>9} {'осцил.':>7} │ "
          f"{'dyn при тихо':>13} │ {'FA':>4} {'HIT':>4} {'MISS':>5} {'CN':>4}")
    print("  " + "-" * 76)

    icaos = sorted(set(r["icao"] for r in rows))
    for thr in thresholds:
        eff_thr = icon_equiv_thresholds(thr, icaos) if per_airport else thr
        res = run_threshold(rows, eff_thr, source, buffer_n)
        if not res:
            continue
        sw = [x["switches"] for x in res]
        osc = sum(1 for x in res if x["switches"] >= 3)

        dyn_calm = [x for x in res if x["regime_at_calm"] == "dynamic"]
        ev = Counter(x["rec"].get("event") for x in dyn_calm)

        print(f"  {thr:>16.1f} │ {statistics.mean(sw):>9.2f} {osc:>7} │ "
              f"{len(dyn_calm):>13} │ {ev['FA']:>4} {ev['HIT']:>4} "
              f"{ev['MISS']:>5} {ev['CN']:>4}")

    if per_airport:
        print(f"\n  Праговете по летище (при METAR-екв. 3.0):")
        for icao in icaos:
            print(f"    {icao}: {icon_equiv_thresholds(3.0, icaos)[icao]:.2f} kt")

    print("\n  ср. прев.   = среден брой превключвания на случай")
    print("  осцил.      = случаи с ≥3 превключвания (нестабилен автомат)")
    print("  dyn при тихо= случаи, в които в НАЙ-ТИХИЯ час режимът е")
    print("                dynamic → охлаждането е потиснато точно когато")
    print("                мъглата би се образувала")


def print_detail(rows, thr, source, buffer_n, only_event=None,
                 per_airport=False):
    icaos = sorted(set(r["icao"] for r in rows))
    eff_thr = icon_equiv_thresholds(thr, icaos) if per_airport else thr
    res = run_threshold(rows, eff_thr, source, buffer_n)
    print(f"\n  ПОДРОБНО при METAR-екв. праг {thr} kt "
          f"(вятър: {source.upper()}, буфер {buffer_n}"
          f"{', байъс-коригиран по летище' if per_airport else ''}):")
    print(f"  {'случай':<28}{'изход':>6}{'прев.':>6}{'dyn/rad':>9}"
          f"{'тих час':>9}{'V':>6}{'режим там':>11}")
    print("  " + "-" * 76)
    sel = [x for x in res
           if only_event is None or x["rec"].get("event") == only_event]
    for x in sorted(sel, key=lambda y: -y["switches"])[:40]:
        r = x["rec"]
        hc = f"{x['h_calm']:02d}h" if x["h_calm"] is not None else "--"
        vc = f"{x['v_calm']:.1f}" if x["v_calm"] is not None else "--"
        ratio = f"{x['n_dyn']}/{x['n_rad']}"
        print(f"  {r['case']:<28}{r.get('event','--'):>6}"
              f"{x['switches']:>6}{ratio:>9}"
              f"{hc:>9}{vc:>6}{x['regime_at_calm'] or '--':>11}")


def main():
    ap = argparse.ArgumentParser(
        description="Симулира почасовия режимен автомат с вятърен праг")
    ap.add_argument("--diag",   default="diagnostic_summary.json")
    ap.add_argument("--source", default="metar", choices=["metar", "icon"])
    ap.add_argument("--buffer", type=int, default=2,
                    help="Последователни несъгласия преди смяна (както в кода)")
    ap.add_argument("--thresholds", type=float, nargs="+",
                    default=[2.5, 3.0, 3.5, 4.0, 5.0, 6.0])
    ap.add_argument("--detail", type=float, default=None,
                    help="Подробен списък при този праг")
    ap.add_argument("--detail-event", default=None,
                    help="Само за този изход в подробния списък")
    ap.add_argument("--all-airports", action="store_true")
    ap.add_argument("--per-airport-bias", action="store_true",
                    help="При --source icon: измества прага по летище с "
                         "измерения METAR-ICON байъс (compare_wind_sources)")
    args = ap.parse_args()

    with open(args.diag, encoding="utf-8") as f:
        diag = json.load(f)

    rows = [r for r in diag
            if not r.get("excluded")
            and (args.all_airports or r["icao"] in CONTINENTAL)]
    for r in rows:
        r["_w"] = hourly_wind(r, args.source)

    n_excl = sum(1 for r in diag if r.get("excluded"))
    scope = "всички летища" if args.all_airports else \
            "континентални (" + ", ".join(CONTINENTAL) + ")"

    per_ab = args.per_airport_bias and args.source == "icon"

    print("=" * 70)
    print(f"  ПОЧАСОВ РЕЖИМЕН АВТОМАТ — {scope}")
    print(f"  Прозорец {WIN_START}–{WIN_END:02d} UTC · вятър: "
          f"{args.source.upper()} · буфер: {args.buffer}"
          f"{'  · байъс-коригиран по летище' if per_ab else ''}")
    print("=" * 70)
    if n_excl:
        print(f"  ({n_excl} валежни случая изключени от целия набор)")
    print("\n  ВНИМАНИЕ: това НЕ е прогноза за CSI. Показва поведението на")
    print("  автомата и кои случаи биха попаднали в dynamic в критичния час.")
    if args.source == "icon" and not per_ab:
        print("\n  ЗАБЕЛЕЖКА: ICON има измерен байъс спрямо METAR (LBSF -1.33,")
        print("  LBPD -1.12, LBGO +0.44 kt). Без --per-airport-bias прагът")
        print("  тук е в 'суров' ICON вятър — сравни с --per-airport-bias.")

    print_table(rows, args.thresholds, args.source, args.buffer, per_ab)

    if args.detail is not None:
        print_detail(rows, args.detail, args.source, args.buffer,
                     args.detail_event, per_ab)


if __name__ == "__main__":
    main()
