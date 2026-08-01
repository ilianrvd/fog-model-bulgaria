# -*- coding: utf-8 -*-
"""
check_threshold.py — стига ли защитимият праг на насищане?
===========================================================
Нула нови пускания. Чете probe_disc.json (от построението с прогностичен
вятър) и отговаря на един въпрос:

  Достигат ли седемте загубени мъглени нощи относителна влажност,
  при която физически защитим праг би задействал кондензация?

    python check_threshold.py

ПРЕДВАРИТЕЛНО ЗАПИСАНИ ПРАГОВЕ (30.07.2026, преди да видим числата):
    0.98  континентален въздух  (София, Г. Оряховица, Пловдив)
    0.97  морски въздух         (Варна, Бургас)

Стойностите са от литературата за активация на капки върху хигроскопични
ядра, НЕ са подбрани от нашите случаи. Ако седемте се връщат при тях —
това е физика. Ако трябва по-ниско — това е нагласяне и промяната се
отказва.

Защо това е важно
-----------------
Моделът няма аерозол — концентрацията на ядра беше мъртва конфигурация
и я махнахме. Значи прагът е сборна замяна за нещо, което не се
моделира, и всяка стойност между 0.95 и 1.00 е еднакво "защитима" на
думи. Затова стойността се фиксира ПРЕДИ измерването и не се пипа след
това.
"""
import sys, os, json, argparse
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError): pass

ap = argparse.ArgumentParser()
ap.add_argument("--json", default="probe_disc.json")
ap.add_argument("--continental", type=float, default=0.98)
ap.add_argument("--maritime", type=float, default=0.97)
ap.add_argument("--current", type=float, default=0.995)
opt = ap.parse_args()

MARITIME = {"LBWN", "LBBG"}

if not os.path.exists(opt.json):
    sys.exit(f"Няма {opt.json}.")

cases = json.load(open(opt.json, encoding="utf-8"))["cases"]


def thr(icao):
    return opt.maritime if icao in MARITIME else opt.continental


print(f"\n{'='*92}")
print(f"  ПРАГ НА НАСИЩАНЕ — стига ли защитимата стойност?")
print(f"  Записано предварително: {opt.continental} континентален, "
      f"{opt.maritime} морски.  Сега: {opt.current}")
print(f"{'='*92}")
print(f"  {'случай':<26} {'изход':<6} {'RH макс':>8} {'праг':>6} "
      f"{'запас':>8} {'часове ≥ праг':>13}   присъда")

groups = {"HIT": [], "MISS": [], "FA": [], "CN": []}
for c in cases:
    rh = [x["rh"] for x in c["rec"] if x.get("rh") is not None]
    if not rh:
        continue
    rh_max = max(rh)
    t = thr(c["icao"])
    n_above = sum(1 for x in rh if x >= t)
    margin = rh_max - t
    would = rh_max >= t
    ev = c["event"]
    groups.setdefault(ev, []).append((c["case"], rh_max, t, margin, n_above, would))

for ev in ("HIT", "MISS", "FA", "CN"):
    for case, rh_max, t, margin, n_above, would in sorted(groups.get(ev, [])):
        verdict = ("щеше да фогне" if would and ev == "MISS" else
                   "остава" if would else
                   "НЕ стига" if ev == "MISS" else "—")
        print(f"  {case:<26} {ev:<6} {100*rh_max:>7.1f}% {100*t:>5.0f}% "
              f"{100*margin:>+7.1f}% {n_above:>13}   {verdict}")

lost = groups.get("MISS", [])
rec = [x for x in lost if x[5]]
print(f"\n{'='*92}")
print("  РАЗБОР")
print(f"{'='*92}")
print(f"  Загубени мъглени нощи: {len(lost)}")
print(f"  От тях биха достигнали защитимия праг: {len(rec)}")
if lost:
    rr = [x[1] for x in lost]
    print(f"  Достигната влажност при загубените: мин {100*min(rr):.1f}%  "
          f"медиана {100*float(np.median(rr)):.1f}%  макс {100*max(rr):.1f}%")

# каква стойност би върнала всичките
if lost:
    need = min(x[1] for x in lost)
    print(f"  За да се върнат ВСИЧКИТЕ, прагът трябва да е ≤ {100*need:.1f}%")

fa = groups.get("FA", []) + groups.get("CN", [])
fa_risk = [x for x in fa if x[5]]
print(f"\n  Обратната страна: нощи без мъгла, които достигат прага: "
      f"{len(fa_risk)} от {len(fa)}")
for case, rh_max, t, margin, n_above, would in fa_risk:
    print(f"    {case}  RH макс {100*rh_max:.1f}%  ({n_above} часа над прага)")

print()
if not lost:
    print("  Няма загубени случаи в този файл — провери дали е от")
    print("  построението с прогностичен вятър.")
elif len(rec) >= len(lost) * 0.6:
    print("  → Защитимият праг връща мнозинството от загубените.")
    print("    Тройката има основание. Следва стенд, после пълен пробег.")
elif rec:
    print(f"  → Защитимият праг връща само {len(rec)} от {len(lost)}.")
    print("    Частично решение. Преценка дали си струва цената в")
    print("    нови фалшиви аларми — виж обратната страна по-горе.")
else:
    print("  → Защитимият праг НЕ връща нито един загубен случай.")
    print("    Те не достигат такава влажност изобщо. По-нисък праг")
    print("    би бил нагласяне срещу тези седем нощи, не физика.")
    print("    По записания критерий промяната се ОТКАЗВА.")
print(f"{'='*92}")
