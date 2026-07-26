# -*- coding: utf-8 -*-
"""Кръпка: fix_soil корекция в verify_cases.run_model.
Същата логика като в run_case: при LBWN (fix_soil=True) морската
ICON T_soil (<5°C) се коригира до T_air_2m + 2°C.
Пусни: python patch_verify_soil.py
"""
import ast, sys

PATH = "verify_cases.py"
s = open(PATH, encoding="utf-8").read()

if "fix_soil" in s:
    print("Вече кръпнато.")
    sys.exit(0)

old = """    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        model.T_skin = min(float(T_soil_icon), model.T[0])"""

new = """    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        # fix_soil (LBWN): ICON морска клетка дава T_soil=0-3°C при
        # реални 8-14°C — коригираме до T_air_2m + 2°C (model.T[0] е
        # приземното ниво от METAR след build_surface_layer)
        if cfg.get("fix_soil") and (model.T_soil - 273.15) < 5.0:
            model.T_soil = max(model.T_soil, float(model.T[0]) + 2.0)
        model.T_skin = min(float(model.T_soil), model.T[0])"""

if old not in s:
    print("Не намирам T_soil блока — провери файла.")
    idx = s.find("T_soil_icon")
    print(repr(s[max(0,idx-50):idx+300]))
    sys.exit(1)

s = s.replace(old, new, 1)

try:
    ast.parse(s)
except SyntaxError as e:
    print("СИНТАКТИЧНА ГРЕШКА — НЕ записвам:", e)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write(s)
print("Приложено: fix_soil в verify_cases.run_model")
print("Проверка: fix_soil =", s.count("fix_soil"))
