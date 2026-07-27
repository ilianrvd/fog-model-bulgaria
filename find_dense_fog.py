# -*- coding: utf-8 -*-
"""
find_dense_fog.py — подрежда случаите по най-ниска НАБЛЮДАВАНА видимост
=======================================================================
Чете cases/*.txt, вади VIS от всеки METAR и показва кои случаи съдържат
реална плътна мъгла. Само чете — нищо не пипа.

Употреба:
    python find_dense_fog.py              всички
    python find_dense_fog.py LBSF         само едно летище
    python find_dense_fog.py --max 500    само под 500 m
"""
import glob, os, re, sys

CASES_DIR = "cases"

# 4-цифрена група за видимост: идва след вятърната група (…KT / …MPS)
_WIND = re.compile(r"\b\d{3}(\d{2,3})(G\d{2,3})?(KT|MPS)\b|\bVRB\d{2}(KT|MPS)\b")
_VIS4 = re.compile(r"\b(\d{4})\b")
_CAVOK = re.compile(r"\bCAVOK\b")


def metar_vis(line):
    """Връща видимост в метри или None."""
    if _CAVOK.search(line):
        return 10000
    m = _WIND.search(line)
    if not m:
        return None
    tail = line[m.end():]
    # Първата 4-цифрена група след вятъра; 9999 = 10km+
    for g in _VIS4.finditer(tail):
        v = int(g.group(1))
        # изключваме очевидни не-видимости (Qxxxx е с префикс Q, тук няма)
        if v <= 9999:
            return 10000 if v == 9999 else v
    return None


def main():
    args = [a for a in sys.argv[1:]]
    vmax = 10001
    if "--max" in args:
        i = args.index("--max")
        vmax = int(args[i + 1])
        del args[i:i + 2]
    icao = args[0].upper() if args else None

    pat = os.path.join(CASES_DIR, f"{icao or 'LB??'}_*_*.txt")
    rows = []
    for path in sorted(glob.glob(pat)):
        vis = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                v = metar_vis(line)
                if v is not None:
                    vis.append(v)
        if not vis:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        rows.append((min(vis), sum(1 for v in vis if v < 1000),
                     sum(1 for v in vis if v < 300), len(vis), name))

    rows = [r for r in rows if r[0] <= vmax]
    rows.sort()

    print(f"{'случай':30}{'мин.VIS':>9}{'<1000m':>8}{'<300m':>7}{'набл.':>7}")
    print("-" * 61)
    for mn, n1000, n300, tot, name in rows:
        print(f"{name:30}{mn:>9}{n1000:>8}{n300:>7}{tot:>7}")
    print("-" * 61)
    print(f"Случаи: {len(rows)}   с наблюдавана VIS < 300m: "
          f"{sum(1 for r in rows if r[0] < 300)}")


if __name__ == "__main__":
    main()
