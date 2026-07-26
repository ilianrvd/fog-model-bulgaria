# -*- coding: utf-8 -*-
"""Диагностичен рун: LBSF 2024-12-06 (CLDY, CN->FA регресия, независима от D4).
Пуска run_case с SEB_DEBUG=1 и записва целия изход в seb_1206.txt.
Пусни: python diag_1206.py
После качи seb_1206.txt в чата.
"""
import os, sys, subprocess

os.environ["SEB_DEBUG"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

with open("seb_1206.txt", "w", encoding="utf-8") as f:
    p = subprocess.run(
        [sys.executable, "run_case.py",
         "--date", "2024-12-06", "--hour", "18", "--hours", "12",
         "--metar-source", "ogimet", "--airports", "LBSF", "--verify"],
        stdout=f, stderr=subprocess.STDOUT,
        env=os.environ,
    )

print("Готово. Изходен код:", p.returncode)
print("Резултатът е в seb_1206.txt — качи го в чата.")
