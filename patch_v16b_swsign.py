# -*- coding: utf-8 -*-
"""
patch_v16b_swsign.py  —  знакът на SW поглъщането в мъгла
==========================================================
ПРОМЕНЯ ФИЗИКАТА. Изисква пълен петлетищен гейт след прилагане.

Дефект (fog_model.py, two_stream_radiation):

    dQ_dt += -np.gradient(SW_dn, z) * absorpt / (rho * cp)
             ↑ грешен знак

SW_dn(z) е ПАДАЩ поток надолу; отслабва надолу през мъглата, значи
расте с височината → dSW_dn/dz > 0. Погълнатото от слой [z, z+dz] е
разликата между влизащото отгоре и излизащото отдолу, тоест
+dSW_dn/dz → ЗАГРЯВАНЕ.

Шаблонът е пренесен от LW члена (ред ~460):

    dQ_lw = -np.gradient(F_up - F_down, z) / (rho * cp)

където (F_up − F_down) е НЕТЕН поток НАГОРЕ и минусът е правилен.
За низходящ поток знакът се обръща.

Последици от дефекта (LBPD 2025-02-25, наблюдавани):
  • при изгрев членът дава −11 до −18 K/hr при z[0]
  • max_cool не го спира: капачката е само върху dQ_lw, преди SW
  • бягство: охлаждане → кондензация → по-плътна мъгла → по-стръмен
    градиент на SW_dn → още "охлаждане". LWP 6e-5 → 1.7e-2 за два часа
  • нощем е невидим (гейт sin_el > 0.01)

ВНИМАНИЕ: v1.1 (праг is_fog), v1.2 (щит dQ_lw ≥ −0.15 K/hr) и
max_cool = 0.8 + 0.4·tanh(LWP/0.02) са калибрирани с този дефект
активен. След поправката подлежат на преразглеждане.
"""
import io, os, shutil, sys

PATH = "fog_model.py"

# Работи и върху чист файл, и върху вече кръпнатия с v16a (диагностика).
VARIANTS = [
    # след patch_v16a_diag.py
    ("        _sw_fog = -np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n",
     "        _sw_fog = np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n"),
    # чист файл
    ("        dQ_dt += -np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n",
     "        dQ_dt += np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n"),
]


def main():
    if not os.path.exists(PATH):
        sys.exit(f"[!] Няма {PATH} в текущата директория.")
    with io.open(PATH, "r", encoding="utf-8", newline="") as f:
        src = f.read()

    hits = [(o, n) for o, n in VARIANTS if src.count(o) == 1]
    if len(hits) != 1:
        sys.exit("[!] Не намерих точно един кандидат за замяна. "
                 "Файлът е друга версия или вече е поправен — прекратявам.")

    old, new = hits[0]
    shutil.copy2(PATH, PATH + ".bak_v16b")
    with io.open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(src.replace(old, new))

    print("[OK] Знакът е поправен. Копие: fog_model.py.bak_v16b")
    print("[!]  ФИЗИКАТА Е ПРОМЕНЕНА — пусни пълния петлетищен гейт.")


if __name__ == "__main__":
    main()
