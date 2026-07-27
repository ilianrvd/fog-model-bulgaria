# -*- coding: utf-8 -*-
"""
patch_v21_per_airport_thresholds.py  —  прагове по летище
==========================================================
Пипа ДВА файла: run_case.py и verify_cases.py, в един diff.

ЗАЩО
----
v20 включи поривния режимен критерий за LBGO и LBPD с глобални прагове
(4 kt / 8 kt). Гейтът показа:

    LBGO   CSI 0.393 → 0.407   MAE_T 2.29 → 2.17   (4 случая, сума −5.62)
    LBPD   CSI 0.316 → 0.316   MAE_T 2.53 → 2.60   (10 случая, сума +2.65)

При Пловдив нула полза по CSI и две MAE_T регресии, и двете DYNM.

Решетъчен скан (scan_thresholds.py --icao LBPD, 264-случаен набор,
критерий „>= 2 поредни часа") обяснява защо:

    G>= |  HIT  MISS   FA   CN
      8 |    0     0    2   12     ← сегашното: 12 CN докоснати за 2 FA
     10 |    0     0    2    7
     12 |    0     0    1    5
     14 |    0     0    1    3
     16 |    0     0    1    1

Нула засегнати HIT при ВСИЧКИ прагове — рискът е нулев по цялата
решетка. Но при G>=8 критерият пипа 12 CN нощи, за да хване 2 FA:
шест към едно намеса срещу полза. Оттам десетте разместени MAE_T.

Регресията LBPD_DYNM_2024-11-04 е CN случай (17/17 CN, MAE_VIS = 0) —
чисто температурна щета. Диагностика: METAR дава 8–10 kt цяла нощ,
ICON дава 2.4–3.7 kt между 20 и 03 UTC. Критерият повярва на
подценения ICON вятър, пусна RADIATIVE две-три часа по-рано и
свободното охлаждане свали модела до 0.1 °C при реални 8 °C.
Поривите в онази нощ са 3.5–9.7 kt → при праг G>=12 случаят изобщо
не се докосва.

ПРОМЯНА
-------
Праговете стават per-airport чрез "gust_thresholds": (V_kt, G_kt) в
AIRPORT_CONFIG, с подразбиране (4.0, 8.0):

    LBWN, LBBG, LBGO   (4.0,  8.0)   — без промяна в поведението
    LBPD               (4.0, 12.0)   — НОВО

Двата reassessment блока четат от конфигурацията вместо зашитите
4.0/8.0.

УГОВОРКА, КОЯТО ДА НЕ СЕ ЗАГУБИ
-------------------------------
Прагът 12 за Пловдив е калибриран върху 1 FA и 3 CN случая от текущия
набор. Това е тънка извадка. Стойността е ПРЕДВАРИТЕЛНА и подлежи на
преразглеждане при разширяване на набора (2024–2026). В публикация да
се описва като такава, с посочен размер на извадката.

Наборът за Пловдив има 37 случая, от които 11 FA и 6 HIT — разширяване
до пълния архив 2024–2026 е следващата стъпка преди затвърждаване.
"""
import io, os, shutil, sys

# ══════════════════════════════════════════════════════════════════════
# 1) AIRPORT_CONFIG — добавяне на gust_thresholds
# ══════════════════════════════════════════════════════════════════════
CFG_OLD = (
    "    \"LBPD\": {\"coastal\": False, \"gust_regime\": True, \"N_d\": 200e6, "
    "\"tau_T\": 3600,  \"tau_qv\": 10800, \"sst_month\": None},\r\n"
)
CFG_NEW = (
    "    # gust_thresholds (V_kt, Gust_kt) — по подразбиране (4.0, 8.0).\r\n"
    "    # LBPD е на 12 kt порив: скан 27.07 показва, че при 8 kt критерият\r\n"
    "    # докосва 12 CN нощи, за да хване 2 FA (шест към едно), и произвежда\r\n"
    "    # температурни регресии в ясните нощи. При 12 kt: 1 FA, 3 CN, 0 HIT.\r\n"
    "    # ICON вятърът в Тракийската низина е ПОДценен (LBPD 2024-11-04:\r\n"
    "    # ICON 2.4-3.7 kt при METAR 8-10 kt), затова ниският праг пуска\r\n"
    "    # RADIATIVE твърде рано. ПРЕДВАРИТЕЛНА стойност — калибрирана върху\r\n"
    "    # 1 FA + 3 CN случая; преразглежда се при разширен набор.\r\n"
    "    \"LBPD\": {\"coastal\": False, \"gust_regime\": True, \"gust_thresholds\": (4.0, 12.0), "
    "\"N_d\": 200e6, \"tau_T\": 3600,  \"tau_qv\": 10800, \"sst_month\": None},\r\n"
)

# ══════════════════════════════════════════════════════════════════════
# 2) run_case.py — четене на праговете
# ══════════════════════════════════════════════════════════════════════
RC_OLD = (
    "                _gust_kt = _cur_wind.get(\"gust10\")\r\n"
    "\r\n"
    "                metar_reassess = dict(metar_dict)\r\n"
    "                if _gust_kt is None:\r\n"
    "                    metar_reassess[\"wind_speed\"] = _cur_wspd_kt\r\n"
    "                elif current_regime == \"dynamic\":\r\n"
    "                    # Излизане само ако И двете са под праг\r\n"
    "                    if _cur_wspd_kt < 4.0 and _gust_kt < 8.0:\r\n"
)
RC_NEW = (
    "                _gust_kt = _cur_wind.get(\"gust10\")\r\n"
    "\r\n"
    "                # v21: прагове по летище. Подразбиране (4, 8);\r\n"
    "                # LBPD е на (4, 12) — виж коментара в AIRPORT_CONFIG.\r\n"
    "                _v_thr, _g_thr = cfg.get(\"gust_thresholds\", (4.0, 8.0))\r\n"
    "\r\n"
    "                metar_reassess = dict(metar_dict)\r\n"
    "                if _gust_kt is None:\r\n"
    "                    metar_reassess[\"wind_speed\"] = _cur_wspd_kt\r\n"
    "                elif current_regime == \"dynamic\":\r\n"
    "                    # Излизане само ако И двете са под праг\r\n"
    "                    if _cur_wspd_kt < _v_thr and _gust_kt < _g_thr:\r\n"
)
RC_OLD2 = (
    "                    # Влизане само ако И двете са над праг\r\n"
    "                    if _cur_wspd_kt >= 4.0 and _gust_kt >= 8.0:\r\n"
)
RC_NEW2 = (
    "                    # Влизане само ако И двете са над праг\r\n"
    "                    if _cur_wspd_kt >= _v_thr and _gust_kt >= _g_thr:\r\n"
)

# ══════════════════════════════════════════════════════════════════════
# 3) verify_cases.py — същото, дословно
# ══════════════════════════════════════════════════════════════════════
VC_OLD = (
    "                _gust_kt = _cur_wind.get(\"gust10\")\r\n"
    "\r\n"
    "                metar_reassess = dict(metar_dict)\r\n"
    "                if _gust_kt is None:\r\n"
    "                    metar_reassess[\"wind_speed\"] = _cur_wspd_kt\r\n"
    "                elif current_regime == \"dynamic\":\r\n"
    "                    # Излизане само ако И двете са под праг\r\n"
    "                    if _cur_wspd_kt < 4.0 and _gust_kt < 8.0:\r\n"
)
VC_NEW = (
    "                _gust_kt = _cur_wind.get(\"gust10\")\r\n"
    "\r\n"
    "                # v21: прагове по летище — ДОСЛОВНО като run_case.py\r\n"
    "                _v_thr, _g_thr = cfg.get(\"gust_thresholds\", (4.0, 8.0))\r\n"
    "\r\n"
    "                metar_reassess = dict(metar_dict)\r\n"
    "                if _gust_kt is None:\r\n"
    "                    metar_reassess[\"wind_speed\"] = _cur_wspd_kt\r\n"
    "                elif current_regime == \"dynamic\":\r\n"
    "                    # Излизане само ако И двете са под праг\r\n"
    "                    if _cur_wspd_kt < _v_thr and _gust_kt < _g_thr:\r\n"
)
VC_OLD2 = RC_OLD2
VC_NEW2 = RC_NEW2

FILES = [
    ("run_case.py",     [("AIRPORT_CONFIG", CFG_OLD, CFG_NEW),
                         ("прагове вход",   RC_OLD,  RC_NEW),
                         ("прагове изход",  RC_OLD2, RC_NEW2)]),
    ("verify_cases.py", [("прагове вход",   VC_OLD,  VC_NEW),
                         ("прагове изход",  VC_OLD2, VC_NEW2)]),
]


def main():
    loaded = {}
    for path, patches in FILES:
        if not os.path.exists(path):
            sys.exit(f"[!] Няма {path}.")
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            src = f.read()
        for name, old, _ in patches:
            n = src.count(old)
            if n != 1:
                sys.exit(f"[!] {path} / '{name}': очаквах 1 съвпадение, "
                         f"намерих {n}. Приложена ли е v20? Нищо не е променено.")
        loaded[path] = src

    for path, patches in FILES:
        shutil.copy2(path, path + ".bak_v21")
        src = loaded[path]
        for _, old, new in patches:
            src = src.replace(old, new)
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        print(f"[OK] {path}  ({len(patches)} блок/а)  копие: {path}.bak_v21")

    a = io.open("run_case.py", encoding="utf-8").read()
    b = io.open("verify_cases.py", encoding="utf-8").read()
    print()
    print(f"[Проверка] run_case:     _v_thr × {a.count('_v_thr')}, "
          f"_g_thr × {a.count('_g_thr')}")
    print(f"[Проверка] verify_cases: _v_thr × {b.count('_v_thr')}, "
          f"_g_thr × {b.count('_g_thr')}")
    if a.count("_v_thr") != b.count("_v_thr"):
        print("[!] РАЗМИНАВАНЕ между двата файла — провери ръчно!")
    print()
    print("[!] Пусни пълния петлетищен гейт. Чети и MAE_T.")


if __name__ == "__main__":
    main()
