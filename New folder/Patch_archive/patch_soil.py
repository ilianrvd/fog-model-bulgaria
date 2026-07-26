# -*- coding: utf-8 -*-
"""Кръпка 4: корекция на T_soil за LBWN (ICON морска клетка).

Варна е в процеп между Варненско езеро и Черно море — ICON при 7 км
резолюция няма наземна клетка при 43.2N (T_soil=0-3°C вместо реални 8-14°C).
Корекция: T_soil = max(T_soil_ICON, T_air_init + 2°C).
Физическа обосновка: почвата при старта е поне 2°C по-топла от въздуха
(термична инерция); T_air_init е коректна (взима се от METAR).

Важи само за LBWN и само когато T_soil < 5°C (ясен признак за морска клетка).

Пусни: python patch_soil.py
"""
import ast, sys

PATH = "run_case.py"
s = open(PATH, encoding="utf-8").read()

if "fix_soil" in s:
    print("Вече кръпнато (fix_soil присъства).")
    sys.exit(0)

changed = []

# 1) Добавяме fix_soil флаг в AIRPORT_CONFIG за LBWN
old_lbwn = '"LBWN": {"sea_sector": (20, 160), "coastal": True,'
new_lbwn = '"LBWN": {"fix_soil": True, "sea_sector": (20, 160), "coastal": True,'
if old_lbwn in s:
    s = s.replace(old_lbwn, new_lbwn, 1)
    changed.append("fix_soil в AIRPORT_CONFIG")
else:
    # fallback без sea_sector (ако кръпка 1 не е минала)
    old_lbwn2 = '"LBWN": {"coastal": True,'
    new_lbwn2 = '"LBWN": {"fix_soil": True, "coastal": True,'
    if old_lbwn2 in s:
        s = s.replace(old_lbwn2, new_lbwn2, 1)
        changed.append("fix_soil в AIRPORT_CONFIG (fallback)")

# 2) Вмъкваме корекцията след реда, където T_soil_K се чете от профила
# Търсим: model.T_soil = float(T_soil_K) или T_soil_K = profile["T_soil"]
# Намираме блока с инициализацията на T_soil
anchor = 'T_soil_icon = profile.get("T_soil")'
if anchor not in s:
    anchor = "T_soil_icon = profile.get('T_soil')"
if anchor not in s:
    idx = s.find('"T_soil"')
    print(f"Не намирам anchor. Контекст:")
    print(repr(s[max(0,idx-100):idx+200]))
    sys.exit(1)

# Намираме края на блока с T_soil инициализация (след print-а)
idx = s.find(anchor)
# Намираме следващия непразен ред след блока (след последния print за T_soil)
block_end = s.find("\n        model.T_skin", idx)
if block_end < 0:
    block_end = s.find("\n        ql_init", idx)
if block_end < 0:
    block_end = s.find("\n\n", idx) + 1
end_of_block = s.find("\n", block_end + 1) + 1

line_start = s.rfind("\n", 0, idx) + 1
ind = s[line_start:idx]

correction = (
f"\n{ind}# Корекция за LBWN: ICON морска клетка дава T_soil=0-3°C при реални 8-14°C.\n"
f"{ind}# Варна е в процеп между езеро и море — нямаме наземна ICON клетка.\n"
f"{ind}if cfg.get('fix_soil'):\n"
f"{ind}    _ts = model.T_soil - 273.15\n"
f"{ind}    _ta = float(profile['T'][0]) - 273.15  # T_air при старта\n"
f"{ind}    if _ts < 5.0:  # морска клетка\n"
f"{ind}        model.T_soil = max(model.T_soil, (_ta + 2.0) + 273.15)\n"
f"{ind}        model.T_skin = min(model.T_skin, model.T_soil)  # Tskin ≤ T_soil\n"
)
s = s[:end_of_block] + correction + s[end_of_block:]
changed.append("T_soil корекция за LBWN")

# Валидация
try:
    ast.parse(s)
except SyntaxError as e:
    print("СИНТАКТИЧНА ГРЕШКА — НЕ записвам:", e)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write(s)
print("Приложено:", changed)
print("Проверка: fix_soil =", s.count("fix_soil"),
      "| T_soil корекция =", s.count("T_air_init + 2"))
