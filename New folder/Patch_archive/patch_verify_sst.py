# -*- coding: utf-8 -*-
"""Кръпка: SST одеало в цикъла на verify_cases.run_model.
verify_cases има собствен integration loop — кръпките в run_case.py
не го засягат. Добавя същото правило: coastal И U<3.5 m/s → T ≥ SST−6.
Пусни: python patch_verify_sst.py
"""
import ast, sys

PATH = "verify_cases.py"
s = open(PATH, encoding="utf-8").read()

if "SST одеало" in s:
    print("Вече кръпнато.")
    sys.exit(0)

# Котва: редът с apply_nudging в цикъла на run_model
anchor = "            apply_nudging(model, hourly_profs[prof_idx],\n                          cfg[\"tau_T\"], current_tau)"
if anchor not in s:
    # опростен вариант на котвата
    anchor = "apply_nudging(model, hourly_profs[prof_idx],"
    idx = s.find(anchor)
    if idx < 0:
        print("Не намирам apply_nudging котвата в run_model.")
        sys.exit(1)
    # намери края на statement-а (следващия ред след затварящата скоба)
    end = s.find(")", idx) + 1
    nl = s.find("\n", end) + 1
    insert_at = nl
else:
    insert_at = s.find(anchor) + len(anchor)
    insert_at = s.find("\n", insert_at) + 1

blanket = '''
        # SST одеало за крайбрежни летища: при слаб вятър (U<3.5 m/s)
        # водата наоколо държи T ≥ SST−6, независимо от посоката.
        # При силен вятър моделът сам не преохлажда (DYNAMIC + смесване).
        if cfg.get("coastal"):
            from run_case import get_sst
            _sst = get_sst(date_str)
            if hourly_profs:
                _wp = hourly_profs[min(prof_idx, len(hourly_profs) - 1)]
                _spd = float(np.hypot(float(_wp["u"][0]), float(_wp["v"][0])))
            else:
                _spd = float(np.hypot(float(model.u[0]), float(model.v[0])))
            if _spd < 3.5:
                _T_fl = (_sst - 6.0) + 273.15
                model.T = np.maximum(model.T, _T_fl)
                model.T_skin = max(model.T_skin, _T_fl - 1.0)
'''

s = s[:insert_at] + blanket + s[insert_at:]

try:
    ast.parse(s)
except SyntaxError as e:
    print("СИНТАКТИЧНА ГРЕШКА — НЕ записвам:", e)
    sys.exit(1)

open(PATH, "w", encoding="utf-8").write(s)
print("Приложено: SST одеало в verify_cases.run_model")
print("Проверка: одеало =", s.count("SST одеало"))
