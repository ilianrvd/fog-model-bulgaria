# -*- coding: utf-8 -*-
"""
patch_v17_fup_tskin.py  —  F↑ в основата да излъчва от повърхността
====================================================================
ПРОМЕНЯ ФИЗИКАТА. Изисква пълен петлетищен гейт.

Дефект (fog_model.py, two_stream_radiation, ред ~440):

    F_up[i] = EMISS_SFC * B[0] * np.exp(-K_EXT_LW * lwp_path[i])
                          ^^^^ B[0] = σ·T_въздух[0]⁴

Коментарът над реда казва "Земна повърхност", но B[0] е емисията на
ПРИЗЕМНИЯ ВЪЗДУХ на z[0] ≈ 0.5 m. T_skin изобщо не влиза в схемата.

Последици:
  • SEB и two_stream имат две различни повърхности: SEB смята
    LW_up = EPS_SFC·σ·T_skin⁴, схемата — от T_air[0]
  • липсва отрицателната обратна връзка: когато въздухът се охлади
    под кожата, F↑ от повърхността трябва да нарасне и да върне
    топлина. Без нея T_skin и T_air се разминават свободно
    (LBPD 2025-02-25: 27 K разлика през половин метър)
  • щитът dQ_lw ≥ −0.15 K/hr (v1.2, 19.07.2026) е въведен именно
    срещу това разкъсване — той лекува симптом на този ред

Промяна: two_stream_radiation приема T_skin (по избор). Когато е
подаден, основата на F↑ излъчва от него. При T_skin=None поведението
е точно старото — обвивките longwave_cooling/solar_heating и всякакъв
външен код не се чупят.

ОЧАКВАН НЕПОСРЕДСТВЕН ЕФЕКТ: малък. При LBGO 2025-02-25 02UTC
разликата B(T_skin) − B(T_air) е ~5 W/m², което дава ~0.2 K/hr.
Смисълът на промяната е обратната връзка, която прави следващата
стъпка (щитът) безопасна. Ако гейтът почти не мръдне — това е
очакваното, не повод да се спре.

ЗАБЕЛЕЖКА за после: EPS_SFC = 0.97 (SEB) и EMISS_SFC = 0.95
(радиация) са две константи за една и съща повърхност. Нарочно НЕ се
уеднаквяват тук — отделна промяна, отделен гейт.
"""
import io, os, shutil, sys

PATH = "fog_model.py"

# ── 1. Сигнатура + докстринг ──────────────────────────────────────────
OLD_SIG = (
    "def two_stream_radiation(T, ql, z, rho, hour_utc, day_of_year=1):\r\n"
    "    \"\"\"\r\n"
    "    Two-stream радиационна схема (LW + SW). Връща dT/dt [K/s].\r\n"
    "\r\n"
    "    LW: F↑(z) = емисия от слоевете под z, ослабена по пътя нагоре.\r\n"
    "        F↓(z) = емисия от атмосферата над z, ослабена надолу.\r\n"
    "        Нагряване = -d(F↑ - F↓)/dz / (ρ·cp)\r\n"
    "\r\n"
    "    SW: Flux отгоре надолу, ослабен от мъглата.\r\n"
    "        Загряване = -dSW/dz · absorption / (ρ·cp)\r\n"
    "    \"\"\"\r\n"
)
NEW_SIG = (
    "def two_stream_radiation(T, ql, z, rho, hour_utc, day_of_year=1,\r\n"
    "                        T_skin=None):\r\n"
    "    \"\"\"\r\n"
    "    Two-stream радиационна схема (LW + SW). Връща dT/dt [K/s].\r\n"
    "\r\n"
    "    LW: F↑(z) = емисия от повърхността + слоевете под z, ослабена\r\n"
    "        по пътя нагоре.\r\n"
    "        F↓(z) = емисия от атмосферата над z, ослабена надолу.\r\n"
    "        Нагряване = -d(F↑ - F↓)/dz / (ρ·cp)\r\n"
    "\r\n"
    "    SW: Flux отгоре надолу, ослабен от мъглата.\r\n"
    "        Загряване = +dSW/dz · absorption / (ρ·cp)\r\n"
    "\r\n"
    "    T_skin [K] — температура на повърхността от SEB. Ако е подадена,\r\n"
    "    основата на F↑ излъчва от нея; иначе (None) се пада обратно на\r\n"
    "    T[0], което е СТАРОТО поведение и се пази само за съвместимост\r\n"
    "    с обвивките longwave_cooling/solar_heating.\r\n"
    "    \"\"\"\r\n"
)

# ── 2. Основата на F_up ───────────────────────────────────────────────
OLD_FUP = (
    "    F_up = np.zeros(nz)\r\n"
    "    for i in range(nz):\r\n"
    "        # Земна повърхност\r\n"
    "        F_up[i] = EMISS_SFC * B[0] * np.exp(-K_EXT_LW * lwp_path[i])\r\n"
)
NEW_FUP = (
    "    # Емисия на ПОВЪРХНОСТТА (не на приземния въздух).\r\n"
    "    # Това е връзката, през която охлаждащият се въздух получава\r\n"
    "    # обратно топлина от по-топлата кожа — отрицателната обратна\r\n"
    "    # връзка, чиято липса позволяваше разкъсване T_skin/T_air.\r\n"
    "    B_sfc = sigma * float(T_skin) ** 4 if T_skin is not None else B[0]\r\n"
    "\r\n"
    "    F_up = np.zeros(nz)\r\n"
    "    for i in range(nz):\r\n"
    "        # Земна повърхност\r\n"
    "        F_up[i] = EMISS_SFC * B_sfc * np.exp(-K_EXT_LW * lwp_path[i])\r\n"
)

# ── 3. Извикването в step() ───────────────────────────────────────────
OLD_CALL = (
    "        dT_rad = two_stream_radiation(\r\n"
    "            T_new, ql_new, self.z, self.rho, hour_now, self.day_of_year)\r\n"
)
NEW_CALL = (
    "        dT_rad = two_stream_radiation(\r\n"
    "            T_new, ql_new, self.z, self.rho, hour_now, self.day_of_year,\r\n"
    "            T_skin=self.T_skin)\r\n"
)

PATCHES = [
    ("сигнатура", OLD_SIG,  NEW_SIG),
    ("основа F_up", OLD_FUP, NEW_FUP),
    ("извикване в step()", OLD_CALL, NEW_CALL),
]


def main():
    if not os.path.exists(PATH):
        sys.exit(f"[!] Няма {PATH} в текущата директория.")
    with io.open(PATH, "r", encoding="utf-8", newline="") as f:
        src = f.read()
    for name, old, _ in PATCHES:
        n = src.count(old)
        if n != 1:
            sys.exit(f"[!] '{name}': очаквах 1 съвпадение, намерих {n}. Прекратявам.")
    shutil.copy2(PATH, PATH + ".bak_v17")
    for _, old, new in PATCHES:
        src = src.replace(old, new)
    with io.open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("[OK] 3 блока. Копие: fog_model.py.bak_v17")
    print("[!]  ФИЗИКАТА Е ПРОМЕНЕНА — пусни пълния петлетищен гейт.")


if __name__ == "__main__":
    main()
