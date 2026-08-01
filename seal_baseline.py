# -*- coding: utf-8 -*-
"""
seal_baseline.py — запечатване на репер
========================================
Превръща JSON от verify_cases пробег в базелайн файлове по станция,
в същата схема, която `--accept` ползва. Не иска повторно пускане.

    python seal_baseline.py verify_2026-07-29_1749.json              # преглед
    python seal_baseline.py verify_2026-07-29_1749.json --write      # записва

По подразбиране НЕ пише нищо — само показва какво би направил.

Старите версии ОСТАВАТ на място. `verify_cases.select_active()` избира
най-високия номер за всяко летище и третира по-старите като архивни,
без да ги мести. Обосновката е в докстринга му (26.07.2026): гейт,
сравняващ срещу всички бази, ражда ~19 фалшиви регресии на пуск.
Местенето им в подпапка би счупило и `--all-baselines`, който глобва
само baselines\\*.json.

Защо не `verify_cases.py --accept`
---------------------------------
`--accept` работи върху резултати в паметта, тоест иска пълен нов пробег
(264 случая). Този скрипт запечатва вече изчисленото. Схемата на изхода е
идентична — същите полета, същият config snapshot.
"""
import sys, os, json, re
from datetime import datetime, timezone
from collections import Counter, defaultdict

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

BASELINE_DIR = "baselines"
VER_RE       = re.compile(r"^(LB[A-Z]{2})-v(\d+)\.json$", re.I)


def next_version(icao, existing):
    """Следваща версия за станция; ако няма стара — v1."""
    vs = [v for i, v in existing if i == icao]
    return max(vs) + 1 if vs else 1


def scan_existing():
    out = []
    if not os.path.isdir(BASELINE_DIR):
        return out
    for fn in os.listdir(BASELINE_DIR):
        m = VER_RE.match(fn)
        if m:
            out.append((m.group(1).upper(), int(m.group(2))))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv
    if not args:
        sys.exit("Употреба: python seal_baseline.py verify_XXX.json [--write]")

    src = args[0]
    d   = json.load(open(src, encoding="utf-8"))
    res = d["results"] if isinstance(d, dict) and "results" in d else d
    cfg = d.get("config", {}) if isinstance(d, dict) else {}

    errs = [r for r in res if "error" in r]
    good = [r for r in res if "error" not in r]

    print(f"\nИЗТОЧНИК: {src}")
    print(f"  пробег   : {d.get('run_utc', '—')}")
    print(f"  случаи   : {len(res)}  (валидни {len(good)}, с грешка {len(errs)})")
    print(f"  md5 модел: {cfg.get('fog_model_md5', '—')}")
    if errs:
        print(f"\n  ВНИМАНИЕ: {len(errs)} случая с грешка НЕ влизат в репера:")
        for r in errs[:10]:
            print(f"    {r.get('case_id', '?')}")
        print("  Реперът ще е непълен — прецени дали да не пуснеш пробега наново.")

    # Групиране по станция
    by_st = defaultdict(dict)
    for r in good:
        by_st[r["icao"]][r["case_id"]] = {
            "event"      : r["eval"]["event"],
            "T_MAE"      : r["eval"]["T"]["MAE"],
            "csi_hourly" : r["eval"]["hourly"]["CSI"],
        }

    existing = scan_existing()
    plan, already = [], []
    for icao in sorted(by_st):
        cases = by_st[icao]
        old   = sorted(vv for ii, vv in existing if ii == icao)
        # Вече запечатано? Сравняваме с най-новия наличен базелайн.
        if old:
            newest = os.path.join(BASELINE_DIR, f"{icao}-v{old[-1]}.json")
            try:
                prev = json.load(open(newest, encoding="utf-8"))["cases"]
                if (len(prev) == len(cases) and
                        all(k in prev and prev[k]["event"] == v["event"]
                            for k, v in cases.items())):
                    already.append((icao, old[-1]))
                    continue
            except (OSError, KeyError, json.JSONDecodeError):
                pass
        plan.append((icao, f"{icao}-v{next_version(icao, existing)}", cases, old))

    if already:
        print(f"\nВЕЧЕ ЗАПЕЧАТАНИ — пропускам")
        for icao, v in already:
            print(f"  {icao:<8} идентично на {icao}-v{v}.json")

    if not plan:
        print(f"\n{'='*70}")
        print("  Няма какво да се запечата — реперът вече е актуален.")
        print(f"{'='*70}")
        return 0

    print(f"\nПЛАН")
    print(f"  {'СТАНЦИЯ':<8} {'НОВ ФАЙЛ':<14} {'СЛУЧАИ':>7}   СЪБИТИЯ"
          f"                       СТАВА АКТИВНА ВМЕСТО")
    for icao, name, cases, old in plan:
        c = Counter(v["event"] for v in cases.values())
        ev = f"H={c['HIT']} M={c['MISS']} FA={c['FA']} CN={c['CN']}"
        arch = f"v{old[-1]}" if old else "— (първа)"
        print(f"  {icao:<8} {name+'.json':<14} {len(cases):>7}   {ev:<28} {arch}")

    tot = Counter(v["event"] for cs in by_st.values() for v in cs.values())
    h, m, fa, cn = tot["HIT"], tot["MISS"], tot["FA"], tot["CN"]
    denom = h + m + fa
    csi_s = f"   CSI={h/denom:.3f}" if denom else ""
    print(f"\n  ЦЯЛ ПРОБЕГ   HIT={h} MISS={m} FA={fa} CN={cn}{csi_s}"
          f"   ({sum(len(c) for c in by_st.values())} случая)")
    if already:
        sub = Counter(v["event"] for _, _, cs, _ in plan for v in cs.values())
        print(f"  ОТ КОИТО НОВИ  HIT={sub['HIT']} MISS={sub['MISS']} "
              f"FA={sub['FA']} CN={sub['CN']}"
              f"   ({sum(len(cs) for _, _, cs, _ in plan)} случая)")

    if not write:
        print(f"\n{'='*70}")
        print("  ПРЕГЛЕД — нищо не е записано.")
        print("  За запис добави --write")
        print(f"{'='*70}")
        return 0

    # ── Запис
    os.makedirs(BASELINE_DIR, exist_ok=True)

    print(f"\nЗАПИС  (старите версии остават на място)")
    for icao, name, cases, old in plan:
        payload = {
            "accepted_utc": datetime.now(timezone.utc).isoformat(),
            "config"      : cfg,
            "cases"       : cases,
        }
        path = os.path.join(BASELINE_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  записан: {path}  ({len(cases)} случая)")

    # ── Контрола: прочитаме обратно и сравняваме
    print(f"\nКОНТРОЛА (обратно прочитане)")
    ok = True
    for icao, name, cases, _ in plan:
        back = json.load(open(os.path.join(BASELINE_DIR, f"{name}.json"),
                              encoding="utf-8"))["cases"]
        same_n  = len(back) == len(cases)
        same_ev = all(back[k]["event"] == v["event"] for k, v in cases.items())
        ok &= same_n and same_ev
        print(f"  {name:<12} случаи {len(back):>3}/{len(cases):<3} "
              f"събития {'съвпадат' if same_ev else 'РАЗЛИЧНИ'}")

    print(f"\n{'='*70}")
    print(f"  {'РЕПЕРЪТ Е ЗАПЕЧАТАН' if ok else 'ГРЕШКА ПРИ КОНТРОЛАТА'}")
    if ok:
        print("  Старите версии са непокътнати; select_active() ще ползва новите.")
    print(f"{'='*70}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
