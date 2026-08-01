# -*- coding: utf-8 -*-
"""
relabel_cases.py — преизчисляване на хедъри и преетикетиране
=============================================================
Чете суровите METAR-и от съществуващите case файлове, преизчислява
статистиките по ВСИЧКИ наблюдения и прилага новите правила от
case_rules.py.

    python relabel_cases.py cases\\*.txt              # преглед
    python relabel_cases.py cases\\*.txt --write      # прилага

По подразбиране НЕ пипа нищо. Суровите METAR-и се пренасят дословно —
менят се само хедърът и, при смяна на категория, името на файла.

ВНИМАНИЕ за реперите
--------------------
Преименуването сменя `case_id` и осиротява записа в базелайните.
Скриптът мигрира и тях (--write), за да не се губи регресионната
история. Старите файлове отиват в cases\\_before_relabel\\.
"""
import sys, os, re, json, glob, shutil, argparse
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_rules as cr

ap = argparse.ArgumentParser()
ap.add_argument("patterns", nargs="+")
ap.add_argument("--write", action="store_true")
ap.add_argument("--baselines", default="baselines")
ap.add_argument("--backup", default=os.path.join("cases", "_before_relabel"))
args = ap.parse_args()

paths = []
for p in args.patterns:
    paths.extend(sorted(glob.glob(p)) or [p])
if not paths:
    sys.exit("Няма намерени файлове.")

NAME_RE = re.compile(r"^(LB[A-Z]{2})_(CFOG|CDRY|CLDY|DYNM)_(\d{4}-\d{2}-\d{2})$")

plan, unchanged, broken = [], [], []

for path in paths:
    stem = os.path.splitext(os.path.basename(path))[0]
    m = NAME_RE.match(stem)
    if not m:
        broken.append((stem, "името не съответства на конвенцията"))
        continue
    icao, old_cat, ds = m.groups()
    try:
        raws = cr.load_raw_metars(path)
        t0, t1 = cr.night_window(ds)
        obs = [o for o in (cr.parse_metar(r, ds) for r in raws)
               if o and t0 <= o["dt"] <= t1]
        if not obs:
            broken.append((stem, "нула разборени METAR-и в прозореца"))
            continue
        st = cr.stats(obs)
        new_cat = cr.classify_preserving(st, old_cat)
    except Exception as e:
        broken.append((stem, f"{type(e).__name__}: {e}"))
        continue

    # старият хедър — за сравнение
    old_min = None
    for line in open(path, encoding="utf-8"):
        if line.startswith("METAR LB") or line.startswith("SPECI LB"):
            break
        if "Мин. видимост" in line:
            mm = re.search(r"(\d+)", line)
            if mm:
                old_min = int(mm.group(1))

    rec = dict(path=path, icao=icao, old_cat=old_cat, new_cat=new_cat,
               ds=ds, st=st, obs=obs, n_file=len(raws), old_min=old_min)
    if new_cat != old_cat:
        plan.append(rec)
    else:
        unchanged.append(rec)

# ── Отчет
print(f"\n{'='*78}")
print(f"  прегледани      : {len(paths)}")
print(f"  за преетикетиране: {len(plan)}")
print(f"  без смяна        : {len(unchanged)}")
print(f"  проблемни        : {len(broken)}")
print(f"{'='*78}")

if plan:
    print("\nПРЕЕТИКЕТИРАНЕ")
    print(f"  {'случай':<30} {'ново':<6} {'vis_min':>8} {'хедър':>7} "
          f"{'явл':>4} {'<1km':>5} {'вятър':>6} {'OVC':>4}")
    for r in sorted(plan, key=lambda x: (x["old_cat"], x["new_cat"], x["path"])):
        st = r["st"]
        vm = "—" if st["vis_min"] is None else f"{st['vis_min']:.0f}"
        om = "—" if r["old_min"] is None else str(r["old_min"])
        print(f"  {r['icao']}_{r['old_cat']}_{r['ds']:<12} → {r['new_cat']:<5} "
              f"{vm:>8} {om:>7} {st['n_fogwx']:>4} {st['n_fogvis']:>5} "
              f"{st['wind_max']:>5.0f}k {st['n_lowovc']:>4}")
    print("\n  разпределение:", dict(Counter(
        f"{r['old_cat']}→{r['new_cat']}" for r in plan)))

# хедъри, които лъжат, дори без смяна на категория
liars = [r for r in unchanged
         if r["old_min"] is not None and r["st"]["vis_min"] is not None
         and abs(r["old_min"] - r["st"]["vis_min"]) > 1
         and not (r["old_min"] == 9999 and r["st"]["vis_min"] == 10000)]
if liars:
    print(f"\nГРЕШНИ ХЕДЪРИ БЕЗ СМЯНА НА КАТЕГОРИЯ: {len(liars)}")
    for r in liars[:15]:
        print(f"  {r['icao']}_{r['old_cat']}_{r['ds']}   "
              f"хедър {r['old_min']} → реално {r['st']['vis_min']:.0f} m")
    if len(liars) > 15:
        print(f"  ... и още {len(liars)-15}")

if broken:
    print(f"\nПРОБЛЕМНИ: {len(broken)}")
    for stem, why in broken[:10]:
        print(f"  {stem}  {why}")

if not args.write:
    print(f"\n{'='*78}")
    print("  ПРЕГЛЕД — нищо не е записано.  За запис добави --write")
    print(f"{'='*78}")
    sys.exit(0)

# ── Запис
os.makedirs(args.backup, exist_ok=True)
renames = {}

for r in plan + unchanged:
    stem_old = f"{r['icao']}_{r['old_cat']}_{r['ds']}"
    stem_new = f"{r['icao']}_{r['new_cat']}_{r['ds']}"
    shutil.copy2(r["path"], os.path.join(args.backup,
                                         os.path.basename(r["path"])))
    text = cr.build_case_text(r["icao"], r["new_cat"], r["ds"], r["obs"])
    new_path = os.path.join(os.path.dirname(r["path"]), stem_new + ".txt")
    # CRLF — както са съществуващите файлове
    with open(new_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(text)
    if new_path != r["path"]:
        os.remove(r["path"])
        renames[stem_old] = stem_new

print(f"\nЗАПИСАНИ: {len(plan)+len(unchanged)} файла "
      f"(копия в {args.backup}\\)")
print(f"ПРЕИМЕНУВАНИ: {len(renames)}")

# ── Миграция на реперите
if renames and os.path.isdir(args.baselines):
    n_mig = 0
    for bp in sorted(glob.glob(os.path.join(args.baselines, "*.json"))):
        try:
            d = json.load(open(bp, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cases = d.get("cases")
        if not isinstance(cases, dict):
            continue
        hits = [k for k in cases if k in renames]
        if not hits:
            continue
        shutil.copy2(bp, bp + ".pre_relabel")
        for k in hits:
            cases[renames[k]] = cases.pop(k)
        json.dump(d, open(bp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        n_mig += len(hits)
        print(f"  репер {os.path.basename(bp)}: {len(hits)} ключа мигрирани")
    print(f"МИГРИРАНИ ЗАПИСА В РЕПЕРИТЕ: {n_mig}  "
          f"(копия с наставка .pre_relabel)")

print(f"\n{'='*78}")
print("  ГОТОВО. Пусни пълен пробег и запечатай нов репер.")
print(f"{'='*78}")
