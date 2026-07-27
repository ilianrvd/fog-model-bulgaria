# -*- coding: utf-8 -*-
"""
patch_v16a_diag.py  —  разбор на dT[0] по членове
==================================================
ЧИСТА ДИАГНОСТИКА. Не променя нито един физически резултат.

Добавя TERM_DEBUG (по образец на съществуващия SEB_DEBUG), който на
кръгъл час печата приноса към T[0] на всеки член поотделно, в K/hr:

    дифузия | LW | SW_мъгла | SW_фон | SEB | микроф | външно(nudging)

"външно" се получава като разлика между T[0] в края на предишната
стъпка и T[0] в началото на текущата — тоест всичко, което run_case.py
прави между извикванията на step(), на практика apply_nudging().

Употреба:
    set TERM_DEBUG=1
    python run_case.py --date 2025-02-25 --hour 18 --hours 16 \
        --airports LBPD --verify --metar-source ogimet
"""
import io, os, shutil, sys

PATH = "fog_model.py"

# ── 1. Флаг + контейнер за радиационните членове ──────────────────────
OLD_FLAG = (
    "import os as _os\r\n"
    "SEB_DEBUG = _os.environ.get(\"SEB_DEBUG\", \"0\") == \"1\"\r\n"
)
NEW_FLAG = (
    "import os as _os\r\n"
    "SEB_DEBUG = _os.environ.get(\"SEB_DEBUG\", \"0\") == \"1\"\r\n"
    "\r\n"
    "# Разбор на dT[0] по членове. Включи с TERM_DEBUG=1.\r\n"
    "TERM_DEBUG = _os.environ.get(\"TERM_DEBUG\", \"0\") == \"1\"\r\n"
    "# Пълни се от two_stream_radiation, чете се от Model.step().\r\n"
    "# Стойностите са K/s при z[0].\r\n"
    "LAST_RAD_TERMS = {\"lw\": 0.0, \"sw_fog\": 0.0, \"sw_bg\": 0.0}\r\n"
)

# ── 2. Разделяне на радиационните членове ─────────────────────────────
OLD_RAD = (
    "    dQ_dt += dQ_lw\r\n"
    "\r\n"
    "    # ── SW flux надолу ──\r\n"
    "    sin_el = _sin_elevation(hour_utc, day_of_year)\r\n"
    "    if sin_el > 0.01:\r\n"
    "        SW_top    = 1361.0 * 0.75 * sin_el\r\n"
    "        tau_SW    = np.exp(-K_EXT_SW * lwp_esc)   # прозрачност от върха до z\r\n"
    "        SW_dn     = SW_top * tau_SW\r\n"
    "        fog_mask  = ql > 1e-5\r\n"
    "        absorpt   = np.where(fog_mask, 1.0 - ALBEDO_FOG, ALPHA_AIR)\r\n"
    "        # В мъглата: поглъщане от дивергенцията на flux-а\r\n"
    "        dQ_dt += -np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n"
    "        # При ясно небе: фонов SW член (водна пара поглъща в целия PBL)\r\n"
    "        # dT/dt_SW = SW_sfc * alpha_bulk / (rho * cp * H_pbl)\r\n"
    "        # alpha_bulk~0.1, H_pbl~1000m → ~0.3 K/hr при обед\r\n"
    "        H_pbl = max(z[-1], 500.0)\r\n"
    "        dQ_dt += SW_top * ALPHA_AIR * 3.0 / (rho * cp * H_pbl)\r\n"
)
NEW_RAD = (
    "    dQ_dt += dQ_lw\r\n"
    "    LAST_RAD_TERMS[\"lw\"] = float(dQ_lw[0])\r\n"
    "    LAST_RAD_TERMS[\"sw_fog\"] = 0.0\r\n"
    "    LAST_RAD_TERMS[\"sw_bg\"] = 0.0\r\n"
    "\r\n"
    "    # ── SW flux надолу ──\r\n"
    "    sin_el = _sin_elevation(hour_utc, day_of_year)\r\n"
    "    if sin_el > 0.01:\r\n"
    "        SW_top    = 1361.0 * 0.75 * sin_el\r\n"
    "        tau_SW    = np.exp(-K_EXT_SW * lwp_esc)   # прозрачност от върха до z\r\n"
    "        SW_dn     = SW_top * tau_SW\r\n"
    "        fog_mask  = ql > 1e-5\r\n"
    "        absorpt   = np.where(fog_mask, 1.0 - ALBEDO_FOG, ALPHA_AIR)\r\n"
    "        # В мъглата: поглъщане от дивергенцията на flux-а\r\n"
    "        _sw_fog = -np.gradient(SW_dn, z) * absorpt / (rho * cp)\r\n"
    "        dQ_dt += _sw_fog\r\n"
    "        LAST_RAD_TERMS[\"sw_fog\"] = float(_sw_fog[0])\r\n"
    "        # При ясно небе: фонов SW член (водна пара поглъща в целия PBL)\r\n"
    "        # dT/dt_SW = SW_sfc * alpha_bulk / (rho * cp * H_pbl)\r\n"
    "        # alpha_bulk~0.1, H_pbl~1000m → ~0.3 K/hr при обед\r\n"
    "        H_pbl = max(z[-1], 500.0)\r\n"
    "        _sw_bg = SW_top * ALPHA_AIR * 3.0 / (rho * cp * H_pbl)\r\n"
    "        dQ_dt += _sw_bg\r\n"
    "        LAST_RAD_TERMS[\"sw_bg\"] = float(np.atleast_1d(_sw_bg)[0])\r\n"
)

# ── 3. step(): начало ─────────────────────────────────────────────────
OLD_S1 = (
    "        T  = self.T.copy()\r\n"
    "        qv = self.qv.copy()\r\n"
    "        ql = self.ql.copy()\r\n"
)
NEW_S1 = (
    "        T  = self.T.copy()\r\n"
    "        qv = self.qv.copy()\r\n"
    "        ql = self.ql.copy()\r\n"
    "\r\n"
    "        # ── TERM_DEBUG: разбор на dT[0] по членове ──\r\n"
    "        _hr_dbg = (self.hour0 + self.time / 3600.0) % 24.0\r\n"
    "        _dbg = TERM_DEBUG and abs(_hr_dbg - round(_hr_dbg)) < 0.009\r\n"
    "        _t_in = float(T[0])\r\n"
    "        # Всичко, което се е случило МЕЖДУ две извиквания на step()\r\n"
    "        # (на практика apply_nudging от run_case.py):\r\n"
    "        _d_ext = _t_in - getattr(self, \"_t0_prev\", _t_in)\r\n"
    "        _d_diff = _d_rad = _d_seb = _d_mic = 0.0\r\n"
)

# ── 4. step(): след дифузия ───────────────────────────────────────────
OLD_S2 = (
    "        ql_new = turbulent_diffusion(ql, Km, self.rho, self.z, self.dt)\r\n"
    "        ql_new = np.maximum(ql_new, 0.0)\r\n"
)
NEW_S2 = (
    "        ql_new = turbulent_diffusion(ql, Km, self.rho, self.z, self.dt)\r\n"
    "        ql_new = np.maximum(ql_new, 0.0)\r\n"
    "        _d_diff = float(T_new[0]) - _t_in\r\n"
)

# ── 5. step(): след радиация ──────────────────────────────────────────
OLD_S3 = "        T_new += dT_rad * self.dt\r\n"
NEW_S3 = (
    "        T_new += dT_rad * self.dt\r\n"
    "        _d_rad = float(dT_rad[0]) * self.dt\r\n"
)

# ── 6. step(): SEB ────────────────────────────────────────────────────
OLD_S4 = "        T_new[0] += H_sfc * self.dt / (self.rho[0] * cp * DZ_EFF_SEB)\r\n"
NEW_S4 = (
    "        _d_seb = H_sfc * self.dt / (self.rho[0] * cp * DZ_EFF_SEB)\r\n"
    "        T_new[0] += _d_seb\r\n"
)

# ── 7. step(): микрофизика ────────────────────────────────────────────
OLD_S5 = (
    "        qv_new += dqv\r\n"
    "        ql_new += dql\r\n"
    "        T_new  += dT_mic\r\n"
)
NEW_S5 = (
    "        qv_new += dqv\r\n"
    "        ql_new += dql\r\n"
    "        T_new  += dT_mic\r\n"
    "        _d_mic = float(dT_mic[0])\r\n"
)

# ── 8. step(): печат + запомняне ──────────────────────────────────────
OLD_S6 = (
    "        # Запазваме\r\n"
    "        self.T  = T_new\r\n"
)
NEW_S6 = (
    "        # ── TERM_DEBUG печат (K/hr при z[0]) ──\r\n"
    "        if _dbg:\r\n"
    "            _k = 3600.0 / self.dt\r\n"
    "            _r = LAST_RAD_TERMS\r\n"
    "            print(f\"    TERM {_hr_dbg:4.1f}h [K/hr @z0] \"\r\n"
    "                  f\"дифуз={_d_diff * _k:+7.2f} \"\r\n"
    "                  f\"LW={_r['lw'] * 3600.0:+7.2f} \"\r\n"
    "                  f\"SWмъгла={_r['sw_fog'] * 3600.0:+8.2f} \"\r\n"
    "                  f\"SWфон={_r['sw_bg'] * 3600.0:+6.2f} \"\r\n"
    "                  f\"SEB={_d_seb * _k:+7.2f} \"\r\n"
    "                  f\"микроф={_d_mic * _k:+7.2f} \"\r\n"
    "                  f\"| външно={_d_ext * _k:+8.2f} \"\r\n"
    "                  f\"| сума={(float(T_new[0]) - _t_in) * _k:+8.2f} \"\r\n"
    "                  f\"DZ={DZ_EFF_SEB:.0f}\", flush=True)\r\n"
    "        self._t0_prev = float(T_new[0])\r\n"
    "\r\n"
    "        # Запазваме\r\n"
    "        self.T  = T_new\r\n"
)

PATCHES = [
    ("флаг TERM_DEBUG",     OLD_FLAG, NEW_FLAG),
    ("радиационни членове", OLD_RAD,  NEW_RAD),
    ("step: начало",        OLD_S1,   NEW_S1),
    ("step: дифузия",       OLD_S2,   NEW_S2),
    ("step: радиация",      OLD_S3,   NEW_S3),
    ("step: SEB",           OLD_S4,   NEW_S4),
    ("step: микрофизика",   OLD_S5,   NEW_S5),
    ("step: печат",         OLD_S6,   NEW_S6),
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
    shutil.copy2(PATH, PATH + ".bak_v16a")
    for _, old, new in PATCHES:
        src = src.replace(old, new)
    with io.open(PATH, "w", encoding="utf-8", newline="") as f:
        f.write(src)
    print("[OK] 8 блока. Копие: fog_model.py.bak_v16a")
    print("[OK] Физиката е непроменена — само диагностика.")


if __name__ == "__main__":
    main()
