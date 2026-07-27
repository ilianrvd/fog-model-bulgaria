# -*- coding: utf-8 -*-
"""
scan_lbsf_thresholds.py — има ли праг, който разделя HIT от FA при София
========================================================================
Разширение на dryrun_gust_regime.py: сканира решетка от прагове (V, G)
само за LBSF и за всяка двойка брои засегнатите HIT и FA случаи.

"Засегнат" = случай с >= 2 поредни часа над двата прага (режимът
изисква 2 поредни за превключване — pending логиката на v1.4).

Търсим двойка с 0 засегнати HIT и > 0 засегнати FA.

Употреба:
    python scan_lbsf_thresholds.py
    python scan_lbsf_thresholds.py --verify logs/verify_2026-07-27_1253.json
"""
import argparse, glob, json, os, re
from collections import defaultdict

KT = 0.5144


def newest_verify():
    fs = sorted(glob.glob(os.path.join("logs", "verify_*.json")))
    if not fs:
        raise SystemExit("[!] Няма logs/verify_*.json")
    return fs[-1]


def consecutive_hits(series, vmin, gmin):
    """Максимален брой ПОРЕДНИ часове с V>=vmin И G>=gmin."""
    best = cur = 0
    for spd, g in series:
        if spd >= vmin and g >= gmin:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", default=None)
    args = ap.parse_args()

    vpath = args.verify or newest_verify()
    with open(vpath, encoding="utf-8") as f:
        vd = json.load(f)
    outcome = {r["case_id"]: r["eval"]["event"]
               for r in vd["results"] if "error" not in r
               and r["case_id"].startswith("LBSF")}
    print(f"[Verify] {os.path.basename(vpath)}   LBSF случаи: {len(outcome)}")

    cache = {}
    for p in glob.glob(os.path.join("icon_cache", "LBSF_*.json")):
        m = re.match(r"LBSF_(\d{4}-\d{2}-\d{2})_", os.path.basename(p))
        if m:
            cache[m.group(1)] = p

    series = {}
    for cid, ev in outcome.items():
        date = cid.split("_")[-1]
        if date not in cache:
            continue
        with open(cache[date], encoding="utf-8") as f:
            prof = json.load(f)
        s = []
        for h in prof.get("hourly_profiles", []):
            u, v, g = h.get("u"), h.get("v"), h.get("gust10")
            if not u or not v or g is None:
                continue
            s.append(((float(u[0])**2 + float(v[0])**2)**0.5 / KT, float(g)))
        if s:
            series[cid] = (ev, s)

    print(f"[Кеш] покрити: {len(series)}")
    print()
    print("Засегнати случаи (>=2 поредни часа над праговете):")
    print(f"{'V>=':>5}{'G>=':>5} | {'HIT':>4}{'MISS':>5}{'FA':>4}{'CN':>5} |  засегнати HIT поименно")
    print("-" * 78)
    for vmin in (4, 5, 6, 7, 8):
        for gmin in (8, 10, 12, 14, 16):
            cnt = defaultdict(int)
            hit_names = []
            for cid, (ev, s) in series.items():
                if consecutive_hits(s, vmin, gmin) >= 2:
                    cnt[ev] += 1
                    if ev == "HIT":
                        hit_names.append(cid.replace("LBSF_", ""))
            names = ", ".join(hit_names) if hit_names else "—"
            print(f"{vmin:>5}{gmin:>5} | {cnt['HIT']:>4}{cnt['MISS']:>5}"
                  f"{cnt['FA']:>4}{cnt['CN']:>5} |  {names}")
        print("-" * 78)


if __name__ == "__main__":
    main()
