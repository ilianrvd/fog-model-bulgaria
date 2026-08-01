# -*- coding: utf-8 -*-
"""
audit_cases.py — одит на етикетите в cases\\
=============================================
Пресмята наново статистиките от хедъра на всеки случай, но върху ВСИЧКИ
METAR-и, и ги сравнява с записаното. Не пипа нищо — само чете и докладва.

Повод: LBGO_CDRY_2025-01-25 има METAR с 450 m FZFG в 05:30, а хедърът
твърди „Мин. видимост: 6000 m" и „METAR-и с VIS < 2000 m: не". Числото
6000 е минимумът само по кръглите часове — конвейерът за етикетиране е
гледал часово, както и метриката.

    python audit_cases.py cases\\*.txt
    python audit_cases.py cases\\*.txt > audit_out.txt
"""
import sys, os, re, glob
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FOG_WX  = re.compile(r"\bFZFG\b|\bBCFG\b|\bMIFG\b|\bPRFG\b|(?<![A-Z])FG\b")
VIS_RE  = re.compile(r"\s(\d{4})(?:NDV)?\s")
TIME_RE = re.compile(r"^METAR LB\w\w \d{2}(\d{2})(\d{2})Z")
EVENT_VIS = 2000


def parse_vis(line):
    if " 9999" in line or "CAVOK" in line:
        return 10000
    m = VIS_RE.search(line)
    return int(m.group(1)) if m else None


def audit(path):
    txt   = open(path, encoding="utf-8").read().splitlines()
    name  = os.path.splitext(os.path.basename(path))[0]
    parts = name.split("_")
    cat   = parts[1] if len(parts) == 3 else "?"

    hdr = {}
    for l in txt:
        if l.startswith("METAR LB"):
            break
        if ":" in l:
            k, _, v = l.partition(":")
            hdr[k.strip()] = v.strip()

    metars = [l.strip() for l in txt if l.startswith("METAR LB")]
    rows = []
    for l in metars:
        m = TIME_RE.match(l)
        if not m:
            continue
        rows.append((f"{m.group(1)}:{m.group(2)}", parse_vis(l),
                     bool(FOG_WX.search(l))))

    if not rows:
        return None

    vis_all  = [v for _, v, _ in rows if v is not None]
    vis_hour = [v for t, v, _ in rows if v is not None and t.endswith(":00")]
    if not vis_all or not vis_hour:
        return None

    min_all, min_hour = min(vis_all), min(vis_hour)
    n_lo_all  = sum(1 for v in vis_all if v < EVENT_VIS)
    n_lo_hour = sum(1 for v in vis_hour if v < EVENT_VIS)
    n_wx      = sum(1 for _, _, w in rows if w)

    # какво твърди хедърът
    hdr_min = None
    for k, v in hdr.items():
        if "Мин. видимост" in k:
            mm = re.search(r"(\d+)", v)
            if mm:
                hdr_min = int(mm.group(1))
    hdr_lo = None
    for k, v in hdr.items():
        if "VIS < 2000" in k:
            hdr_lo = v.strip().lower().startswith("да")

    def _same_vis(a, b):
        # 9999 в METAR и 10000 след нормализация са едно и също
        return a == b or {a, b} == {9999, 10000}

    flags = []
    if hdr_min is not None and not _same_vis(hdr_min, min_all):
        flags.append(f"хедър min={hdr_min} → реално {min_all}")
    if hdr_lo is not None and hdr_lo != (n_lo_all > 0):
        flags.append(f"хедър 'VIS<2000: {'да' if hdr_lo else 'не'}' → реално "
                     f"{n_lo_all} наблюдения")
    if cat == "CDRY" and n_lo_all > 0:
        flags.append(f"CDRY, но {n_lo_all} METAR-а под {EVENT_VIS} m")
    if cat == "CDRY" and n_wx > 0:
        flags.append(f"CDRY, но {n_wx} METAR-а с мъглено явление")
    if min_hour != min_all:
        flags.append(f"минимумът е на половинчасов METAR "
                     f"({min_all} m срещу {min_hour} m по кръгли часове)")

    return {"name": name, "cat": cat, "n": len(rows),
            "min_all": min_all, "min_hour": min_hour,
            "n_lo_all": n_lo_all, "n_lo_hour": n_lo_hour,
            "n_wx": n_wx, "flags": flags}


paths = []
for a in sys.argv[1:]:
    paths.extend(sorted(glob.glob(a)) or [a])
if not paths:
    sys.exit("Употреба: python audit_cases.py cases\\*.txt")

res = [r for r in (audit(p) for p in paths) if r]
suspect = [r for r in res if r["flags"]]

for r in suspect:
    print(f"\n{r['name']}  ({r['cat']}, {r['n']} METAR-а)")
    for f in r["flags"]:
        print(f"    • {f}")

hidden = [r for r in res if r["n_lo_all"] > 0 and r["n_lo_hour"] == 0]
wx_only = [r for r in res if r["n_wx"] > 0 and r["n_lo_all"] == 0]

print(f"\n{'='*72}")
print(f"  случаи                                   : {len(res)}")
print(f"  със сигнал                               : {len(suspect)}")
print(f"  минимумът е на половинчасов METAR        : "
      f"{sum(1 for r in res if r['min_all'] != r['min_hour'])}")
print(f"  мъгла ИЗЦЯЛО невидима за часовия поглед  : {len(hidden)}")
print(f"  мъглено явление без VIS < 2000           : {len(wx_only)}")
print()
print("  ПО КАТЕГОРИЯ (случаи със сигнал)")
for c, n in sorted(Counter(r["cat"] for r in suspect).items(),
                   key=lambda x: -x[1]):
    tot = sum(1 for r in res if r["cat"] == c)
    print(f"    {c:<6} {n:>3} от {tot:>3}")
if hidden:
    print()
    print("  СЛУЧАИ, КЪДЕТО ЧАСОВИЯТ ПОГЛЕД НЕ ВИЖДА МЪГЛАТА ИЗОБЩО")
    for r in hidden:
        print(f"    {r['name']:<34} {r['cat']:<5} min={r['min_all']:>5} m")
print(f"{'='*72}")
