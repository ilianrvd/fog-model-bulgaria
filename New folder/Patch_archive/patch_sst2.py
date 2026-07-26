# -*- coding: utf-8 -*-
"""Кръпка 2: посоката/скоростта за SST floor от ICON почасовия профил,
не от вътрешния вятър на модела (той дрейфва при щил).
Пусни: python patch_sst2.py
"""
import ast, sys

PATH = "run_case.py"
s = open(PATH, encoding="utf-8").read()

old = "    _u0, _v0 = float(model.u[0]), float(model.v[0])"
# намираме реда с отстъпа, какъвто е (кръпка 1 го сложи с отстъпа на блока)
idx = s.find("_u0, _v0 = float(model.u[0]), float(model.v[0])")
if idx < 0:
    print("Не намирам реда за подмяна — вече кръпнато или различен файл.")
    sys.exit(1)

# отстъп на реда
line_start = s.rfind("\n", 0, idx) + 1
ind = s[line_start:idx]

old_line = ind + "_u0, _v0 = float(model.u[0]), float(model.v[0])"
new_lines = (
    ind + "# вятър от ICON почасовия профил — стабилен; моделният дрейфва при щил\n" +
    ind + "if hourly_profs:\n" +
    ind + "    _wp = hourly_profs[min(prof_idx, len(hourly_profs) - 1)]\n" +
    ind + "    _u0, _v0 = float(_wp[\"u\"][0]), float(_wp[\"v\"][0])\n" +
    ind + "else:\n" +
    ind + "    _u0, _v0 = float(model.u[0]), float(model.v[0])"
)

s = s.replace(old_line, new_lines, 1)

try:
    ast.parse(s)
except SyntaxError as e:
    print("СИНТАКТИЧНА ГРЕШКА — НЕ записвам:", e)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write(s)
print("Приложено: floor вятърът идва от ICON профила")
print("Проверка: _wp =", s.count("_wp["), "| model.u fallback =", s.count("_u0, _v0 = float(model.u[0])"))
