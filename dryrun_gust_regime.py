# -*- coding: utf-8 -*-
"""
dryrun_gust_regime.py  —  предсказание без промяна в кода
==========================================================
Отговаря на въпроса „какво би направил поривният режимен критерий при
София, Пловдив и Горна", БЕЗ да се пипа fog_model.py или run_case.py.

Чете icon_cache/ и последния verify JSON, прилага критерия от v1.4
върху всеки час и брои колко часа биха превключили в DYNAMIC —
разбито по изхода на случая (HIT / MISS / FA / CN).

Критерият (както е за крайбрежните, run_case.py ред ~848):
    влизане в DYNAMIC :  V >= 4 kt  И  Gust >= 8 kt
    излизане          :  V <  4 kt  И  Gust <  8 kt
    смесено           :  запазва текущия режим

ЧЕТЕНЕ НА РЕЗУЛТАТА
-------------------
Ако HIT случаите имат МАЛКО часове с превключване, а FA случаите —
МНОГО, критерият е обещаващ: ще убива фалшивите тревоги, без да губи
попаденията.

Ако HIT случаите също имат много — ще платим с HIT-ове, точно както
при v19.

Употреба:
    python dryrun_gust_regime.py
    python dryrun_gust_regime.py --v 4 --g 8      (други прагове)
"""
import argparse, glob, json, os, re
from collections import defaultdict

KT = 0.5144  # m/s на възел


def newest_verify():
    fs = sorted(glob.glob(os.path.join("logs", "verify_*.json")))
    if not fs:
        raise SystemExit("[!] Няма logs/verify_*.json")
    return fs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v", type=float, default=4.0, help="праг за средния вятър [kt]")
    ap.add_argument("--g", type=float, default=8.0, help="праг за порива [kt]")
    ap.add_argument("--verify", default=None, help="кой verify JSON да се ползва")
    args = ap.parse_args()

    vpath = args.verify or newest_verify()
    with open(vpath, encoding="utf-8") as f:
        vd = json.load(f)
    outcome = {r["case_id"]: r["eval"]["event"]
               for r in vd["results"] if "error" not in r}
    print(f"[Verify] {os.path.basename(vpath)}   случаи: {len(outcome)}")
    print(f"[Критерий] V >= {args.v:.1f} kt  И  Gust >= {args.g:.1f} kt")
    print()

    # индекс на кеша: ICAO_YYYY-MM-DD_* → път
    cache = {}
    for p in glob.glob(os.path.join("icon_cache", "*.json")):
        m = re.match(r"(LB[A-Z]{2})_(\d{4}-\d{2}-\d{2})_", os.path.basename(p))
        if m:
            cache[(m.group(1), m.group(2))] = p

    stats = defaultdict(lambda: defaultdict(list))
    missing = 0

    for cid, ev in outcome.items():
        parts = cid.split("_")
        if len(parts) < 3:
            continue
        icao, date = parts[0], parts[-1]
        if icao in ("LBWN", "LBBG"):
            continue                      # вече ползват критерия
        key = (icao, date)
        if key not in cache:
            missing += 1
            continue
        with open(cache[key], encoding="utf-8") as f:
            prof = json.load(f)
        hrs = prof.get("hourly_profiles", [])
        n_flip = 0
        n_tot = 0
        for h in hrs:
            u = h.get("u"); v = h.get("v"); g = h.get("gust10")
            if not u or not v or g is None:
                continue
            spd_kt = (float(u[0]) ** 2 + float(v[0]) ** 2) ** 0.5 / KT
            n_tot += 1
            if spd_kt >= args.v and float(g) >= args.g:
                n_flip += 1
        if n_tot:
            stats[icao][ev].append((n_flip, n_tot, cid))

    if missing:
        print(f"[!] Без кеш: {missing} случая\n")

    hdr = f"{'ICAO':6}{'изход':7}{'случаи':>8}{'ср. часове DYN':>16}{'% от часовете':>15}{'случаи с 0':>12}"
    print(hdr)
    print("-" * len(hdr))
    tot = defaultdict(lambda: [0, 0, 0, 0])
    for icao in sorted(stats):
        for ev in ("HIT", "MISS", "FA", "CN"):
            rows = stats[icao].get(ev, [])
            if not rows:
                continue
            f_sum = sum(r[0] for r in rows)
            t_sum = sum(r[1] for r in rows)
            zeros = sum(1 for r in rows if r[0] == 0)
            print(f"{icao:6}{ev:7}{len(rows):>8}{f_sum/len(rows):>16.1f}"
                  f"{100*f_sum/t_sum:>14.1f}%{zeros:>12}")
            t = tot[ev]
            t[0] += len(rows); t[1] += f_sum; t[2] += t_sum; t[3] += zeros
    print("-" * len(hdr))
    for ev in ("HIT", "MISS", "FA", "CN"):
        if tot[ev][0]:
            n, fs, ts, z = tot[ev]
            print(f"{'ВСИЧКИ':6}{ev:7}{n:>8}{fs/n:>16.1f}{100*fs/ts:>14.1f}%{z:>12}")

    print()
    print("Най-засегнатите HIT случаи (риск от загуба):")
    risky = []
    for icao in stats:
        for r in stats[icao].get("HIT", []):
            risky.append(r)
    for n_flip, n_tot, cid in sorted(risky, reverse=True)[:8]:
        print(f"  {cid:30} {n_flip:>3}/{n_tot} часа")

    print()
    print("Най-засегнатите FA случаи (потенциална печалба):")
    gain = []
    for icao in stats:
        for r in stats[icao].get("FA", []):
            gain.append(r)
    for n_flip, n_tot, cid in sorted(gain, reverse=True)[:8]:
        print(f"  {cid:30} {n_flip:>3}/{n_tot} часа")


if __name__ == "__main__":
    main()
