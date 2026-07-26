# -*- coding: utf-8 -*-
"""Диагностичен рун: LBSF 2024-01-19 (CLDY, HIT->MISS след D4).
Пуска run_case с SEB_DEBUG=1 и записва целия изход в seb_0119.txt.
Пусни: python diag_0119.py
После качи seb_0119.txt в чата.
"""
import os, sys, subprocess

os.environ["SEB_DEBUG"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

with open("seb_0119.txt", "w", encoding="utf-8") as f:
    p = subprocess.run(
        [sys.executable, "run_case.py",
         "--date", "2024-01-19", "--hour", "18", "--hours", "12",
         "--metar-source", "ogimet", "--airports", "LBSF", "--verify"],
        stdout=f, stderr=subprocess.STDOUT,
        env=os.environ,
    )

print("Готово. Изходен код:", p.returncode)
print("Резултатът е в seb_0119.txt — качи го в чата.")
