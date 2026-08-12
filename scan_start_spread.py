"""
scan_start_spread.py
=====================
За списък от case_id извлича стартовия METAR (18 UTC) и печата T, Td,
спреда. Цели да отговори: тръгват ли FA случаите близо до насищане
(тесен спред), или се развалят по пътя (широк спред при старт)?

Употреба:
  python scan_start_spread.py --diag diagnostic_summary.json --cases cases/ --icao LBSF --event FA
"""
import argparse, json, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def start_td(path, hour0=18):
    best = None
    best_diff = 10**9
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("METAR "):
                continue
            m = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', line)
            if not m:
                continue
            h, mn = int(m.group(2)), int(m.group(3))
            diff = abs(h*60+mn - hour0*60)
            mt = re.search(r'\b(M?\d{2})/(M?\d{2})\b', line)
            if not mt:
                continue
            if diff < best_diff:
                def _t(s): return -int(s[1:]) if s.startswith("M") else int(s)
                best = (_t(mt.group(1)), _t(mt.group(2)), h, mn)
                best_diff = diff
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", default="diagnostic_summary.json")
    ap.add_argument("--cases", default="cases")
    ap.add_argument("--icao", default=None)
    ap.add_argument("--event", default=None)
    ap.add_argument("--hour", type=int, default=18)
    args = ap.parse_args()

    with open(args.diag, encoding="utf-8") as f:
        diag = json.load(f)
    rows = [r for r in diag if not r.get("excluded")]
    if args.icao:
        rows = [r for r in rows if r["icao"] == args.icao]
    if args.event:
        rows = [r for r in rows if r["event"] == args.event]

    print(f"{'случай':<26}{'T':>5}{'Td':>5}{'спред':>7}{'Tmin_err':>10}")
    print("-" * 55)
    n_tight, n_wide = 0, 0
    for r in sorted(rows, key=lambda x: x["case"]):
        path = os.path.join(args.cases, r["case"] + ".txt")
        st = start_td(path, args.hour)
        if st is None:
            print(f"{r['case']:<26}  -- няма METAR при старт --")
            continue
        T, Td, h, mn = st
        spread = T - Td
        tag = "TIGHT" if spread <= 1 else ("WIDE" if spread >= 3 else "  mid")
        if spread <= 1: n_tight += 1
        if spread >= 3: n_wide += 1
        terr = r.get("Tmin_err")
        terr_s = f"{terr:+.2f}" if terr is not None else "n/a"
        print(f"{r['case']:<26}{T:>5}{Td:>5}{spread:>7}{terr_s:>10}  {tag}")

    print("-" * 55)
    print(f"TIGHT (спред<=1°C): {n_tight}   WIDE (спред>=3°C): {n_wide}   "
          f"общо: {len(rows)}")


if __name__ == "__main__":
    main()
