# -*- coding: utf-8 -*-
"""Кръпка 3: SST floor = "одеало" само при слаб вятър.

Логика (замества посочно-зависимата от кръпка 1+2):
  - coastal И U < 3.5 m/s  → floor = SST − 6  (водата наоколо се усеща
    при щил/слаб вятър независимо от посоката; посоката при слаб вятър
    е нестабилна и нерепрезентативна)
  - иначе → без floor: при силен вятър моделът сам не преохлажда
    (DYNAMIC режим + турбулентно смесване; LBWN 25.12: U=10-14 m/s,
    T грешка <2°C без никакъв floor)

Вятърът за проверката: от ICON почасовия профил (стабилен),
с fallback към вътрешния вятър на модела.

Пусни: python patch_sst3.py
"""
import ast, re, sys

PATH = "run_case.py"
s = open(PATH, encoding="utf-8").read()

if "одеало" in s:
    print("Вече кръпнато (кръпка 3 присъства).")
    sys.exit(0)

# Хващаме целия coastal блок от кръпка 1+2: от "if cfg.get(\"coastal\"):"
# до последния ред "model.T_skin = max(model.T_skin, _T_fl - 1.0)"
pat = re.compile(
    r'( *)if cfg\.get\("coastal"\):\n'
    r'(?:.*\n)*?'
    r'.*model\.T_skin = max\(model\.T_skin, _T_fl - 1\.0\)\n'
)
m = pat.search(s)
if not m:
    print("Не намирам coastal блока от кръпки 1+2 — провери файла.")
    sys.exit(1)

ind = m.group(1)
body = (
f'''{ind}if cfg.get("coastal"):
{ind}    # SST floor = "одеало" САМО при слаб вятър. При щил/слаб вятър
{ind}    # водата наоколо (море, езеро) се усеща независимо от посоката;
{ind}    # при силен вятър моделът сам не преохлажда (DYNAMIC + смесване),
{ind}    # а посоката при слаб вятър е нестабилна — затова без сектори.
{ind}    sst = get_sst(date_str)
{ind}    if hourly_profs:
{ind}        _wp = hourly_profs[min(prof_idx, len(hourly_profs) - 1)]
{ind}        _spd = float(np.hypot(float(_wp["u"][0]), float(_wp["v"][0])))
{ind}    else:
{ind}        _spd = float(np.hypot(float(model.u[0]), float(model.v[0])))
{ind}    if _spd < 3.5:
{ind}        _T_fl = (sst - 6.0) + 273.15
{ind}        model.T = np.maximum(model.T, _T_fl)
{ind}        model.T_skin = max(model.T_skin, _T_fl - 1.0)
'''
)
s = s[:m.start()] + body + s[m.end():]

try:
    ast.parse(s)
except SyntaxError as e:
    print("СИНТАКТИЧНА ГРЕШКА — НЕ записвам:", e)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write(s)
print("Приложено: SST одеало при U<3.5 m/s (floor=SST−6), без сектори")
print("Проверка: одеало =", s.count("одеало"), "| _onshore остатъци =", s.count("_onshore"))
