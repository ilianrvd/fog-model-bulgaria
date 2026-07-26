"""
fog_model.py
============
Едномерен (вертикален) модел за прогнозиране на мъгла и видимост
за летище София (LBSF), базиран на физиката на PAFOG.

Автор: ДП РВД / Aviation MET
Версия: 1.0

Физически процеси:
  - Радиационно охлаждане (дългова радиация по опростена схема)
  - Турбулентна дифузия (TKE-based K-theory)
  - Кондензация/изпарение (bulk microphysics)
  - Утаяване на капките (stokes settling)
  - Приземно-слоева параметризация (Louis 1979)

Вход:
  - WRF NetCDF профил (T, QVAPOR, U, V, P, PH, PHB)
  - METAR наблюдение за инициализация

Изход:
  - 12-часова прогноза: LWC, видимост, RH, T по нива
  - CSV и визуализация
"""

import numpy as np
from scipy.integrate import solve_ivp
from scipy.interpolate import interp1d
import warnings
warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────────────────────
# Физически константи
# ──────────────────────────────────────────────────────────────────────────────
Rd    = 287.05    # J/(kg·K)  - газова константа на сух въздух
Rv    = 461.5     # J/(kg·K)  - газова константа на водни пари
cp    = 1005.0    # J/(kg·K)  - специфичен топлинен капацитет
Lv    = 2.5e6     # J/kg      - латентна топлина на кондензацията
g     = 9.81      # m/s²
kappa = Rd / cp   # ≈ 0.2857
sigma = 5.67e-8   # W/(m²·K⁴) - Stefan-Boltzmann
rho_w = 1000.0    # kg/m³     - плътност на водата
eps   = Rv / Rd   # ≈ 1.608  (обърнато за формули)
eps_r = Rd / Rv   # ≈ 0.622

# Параметри на микрофизиката
N_d       = 100e6   # m⁻³  - брой концентрация на CCN
r_eff_min = 2e-6    # m    - минимален ефективен радиус
r_eff_max = 20e-6   # m    - максимален ефективен радиус
beta_vis  = 144.7   # коефициент видимост-LWC (Kunkel 1984)

# ──────────────────────────────────────────────────────────────────────────────
# Параметри на почвата (Force-Restore схема, Deardorff 1978)
# ──────────────────────────────────────────────────────────────────────────────
C_soil  = 1.2e6      # J/m³/K  — топлинен капацитет на почвата
Lambda  = 0.5        # W/m/K   — топлопроводимост (суха почва)
d_soil  = 0.10       # m       — дебелина на повърхностния слой
omega_d = 2*np.pi/86400.0  # rad/s — дневна честота

# Радиационни параметри
emiss_air = 0.85    # средна излъчвателна способност на въздуха
emiss_fog = 1.0     # мъглата е почти черно тяло в IR

# ──────────────────────────────────────────────────────────────────────────────
# Помощни термодинамични функции
# ──────────────────────────────────────────────────────────────────────────────

def sat_vapor_pressure(T_K: np.ndarray) -> np.ndarray:
    """Наситено парно налягане [Pa] по формулата на Buck (1981)."""
    T_C = T_K - 273.15
    es = 611.2 * np.exp(17.67 * T_C / (T_C + 243.5))
    return es


def sat_mixing_ratio(T_K: np.ndarray, p_Pa: np.ndarray) -> np.ndarray:
    """Наситено смесително съотношение [kg/kg]."""
    es = sat_vapor_pressure(T_K)
    qs = eps_r * es / (p_Pa - es)
    return np.maximum(qs, 0.0)


def relative_humidity(qv: np.ndarray, T_K: np.ndarray, p_Pa: np.ndarray) -> np.ndarray:
    """Относителна влажност [0..1]."""
    qs = sat_mixing_ratio(T_K, p_Pa)
    return np.clip(qv / (qs + 1e-12), 0.0, 1.5)


def dew_point_from_rh(T_K: float, rh: float) -> float:
    """Точка на росата [K] по Магнус."""
    T_C = T_K - 273.15
    a, b = 17.27, 237.3
    gamma = (a * T_C) / (b + T_C) + np.log(max(rh, 0.01))
    Td_C = b * gamma / (a - gamma)
    return Td_C + 273.15


def theta_to_T(theta: np.ndarray, p_Pa: np.ndarray, p0: float = 1e5) -> np.ndarray:
    """Потенциална → абсолютна температура."""
    return theta * (p_Pa / p0) ** kappa


def T_to_theta(T_K: np.ndarray, p_Pa: np.ndarray, p0: float = 1e5) -> np.ndarray:
    """Абсолютна → потенциална температура."""
    return T_K * (p0 / p_Pa) ** kappa


def virtual_potential_temp(theta: np.ndarray, qv: np.ndarray, ql: np.ndarray) -> np.ndarray:
    """Виртуална потенциална температура."""
    return theta * (1.0 + (Rv / Rd) * qv - ql)


# ──────────────────────────────────────────────────────────────────────────────
# Видимост от LWC
# ──────────────────────────────────────────────────────────────────────────────

def lwc_to_visibility(lwc: np.ndarray) -> np.ndarray:
    """
    Видимост [m] от LWC [kg/m³] по Kunkel (1984):
      VIS = beta * LWC^(-0.65)
    При LWC < праг → "ясно"
    """
    lwc_g = lwc * 1000.0  # → g/m³
    vis = np.where(
        lwc_g > 0.005,
        beta_vis * lwc_g ** (-0.65),
        10000.0          # видимост > 10 km
    )
    return np.clip(vis, 0.0, 10000.0)


def vis_to_metar_category(vis_m: float) -> str:
    """ICAO MET категория по видимост."""
    if vis_m < 200:   return "LIFR"
    if vis_m < 600:   return "IFR"
    if vis_m < 800:   return "MVFR"
    return "VFR"


# ──────────────────────────────────────────────────────────────────────────────
# Турбулентна параметризация
# ──────────────────────────────────────────────────────────────────────────────

def louis_stability_function(Ri: np.ndarray,
                              z: np.ndarray,
                              S: np.ndarray = None,
                              z0: float = 0.1) -> np.ndarray:
    """
    Функция за устойчивост по Louis (1979).
    Връща K_m [m²/s] за всяко ниво.

    Параметри
    ----------
    Ri : bulk Richardson number
    z  : височина [m]
    S  : вертикален shear на вятъра |dU/dz| [1/s] — от bulk_richardson_and_shear()
         Ако None → използва фонов shear 0.01 /s (~ 1 m/s на 100m)
    """
    kv = 0.4
    z_safe = np.maximum(z, 0.01)
    l = kv * z_safe / (1.0 + kv * z_safe / 200.0)

    # Функция за устойчивост fm(Ri) по Louis (1979)
    # Нестабилен (Ri < 0): fm > 1 → усилена турбуленция
    # Стабилен  (Ri > 0): fm < 1 → потисната турбуленция → 0 при силна инверсия
    fm = np.where(
        Ri < 0,
        1.0 - 5.0 * Ri / (1.0 + 75.0 * kv**2 * np.sqrt(np.abs(Ri) + 1e-6)),
        1.0 / (1.0 + 5.0 * Ri) ** 2     # монотонно намалява: Ri=0→1, Ri=1→0.028
    )
    fm = np.clip(fm, 0.0, 5.0)

    # Реален shear |dU/dz| [1/s] с минимален фонов член
    # Фонов shear 0.01/s ≈ 1 m/s на 100m — при абсолютен покой
    if S is not None:
        shear = np.maximum(S, 0.01)
    else:
        shear = np.full_like(z, 0.01)

    # Km = l² · |dU/dz| · fm(Ri)  [m²/s]
    Km = l ** 2 * shear * fm
    return np.clip(Km, 1e-4, 5.0)


def bulk_richardson(theta_v: np.ndarray, u: np.ndarray,
                    v: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Bulk Richardson number между нивата (backward compatible)."""
    Ri, _ = bulk_richardson_and_shear(theta_v, u, v, z)
    return Ri
def solar_sw_down(hour_utc: float, day_of_year: int) -> float:
    """Късовълнова радиация надолу [W/m²] от слънчевата елевация."""
    s = _sin_elevation(hour_utc, day_of_year)
    return 900.0 * max(s, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Surface Energy Balance (SEB) — прогностична T_skin
# Дизайн: Fable5 одит 2026-07-10. Литература: Duynkerke (1991), COBEL-ISBA.
# ──────────────────────────────────────────────────────────────────────────────

C_SKIN   = 2.0e4     # J/m²/K
C_H_BULK = 1.2e-3    # bulk аеродинамичен коефициент (константа)
EPS_SFC  = 0.97      # емисивност на повърхността
ALBEDO   = 0.20      # албедо
LAMBDA_G = 0.5       # W/m/K
D_SOIL_G = 0.05      # m
U_MIN    = 0.3       # m/s
C_SOIL_LAYER = 1.2e6 * 0.10   # J/m²/K — Force-Restore T_soil

# Диагностика на SEB бюджета. Логва член по член нощем.
# Включи с fog_model.SEB_DEBUG = True, или SEB_DEBUG=1 в средата.
import os as _os
SEB_DEBUG = _os.environ.get("SEB_DEBUG", "0") == "1"


def seb_step(T_skin: float, T_soil: float,
             T_air0: float, qv0: float, p0: float, rho0: float,
             U0: float, lwp_col: float,
             sw_down: float, dt: float, hour_utc: float = -1.0,
             T_sky: float = None, cf: float = 0.0) -> tuple:
    """
    Една стъпка на Surface Energy Balance.
    Връща: (T_skin_new, H, E_dew)
      H     : сензибилен поток [W/m²], положителен = повърхност → въздух
      E_dew : поток на роса [kg/m²/s]
    """
    # 1. Парно налягане за Brunt
    e_Pa  = qv0 * p0 / (eps_r + qv0 * (1 - eps_r))
    e_hPa = max(e_Pa / 100.0, 0.1)

    # 2. Атмосферна емисивност.
    #    МЪГЛА: черно тяло (eps_a=1) — пълно fog-top охлаждане.
    #    ИНАЧЕ: Crawford & Duchon (1999): eps = cf + (1−cf)·eps_clear,
    #    с eps_clear по Prata (1996). Облачността (cf от ICON) носи
    #    топлия LW_down в облачни нощи; ясните нощи охлаждат пълноценно.
    # Праг 0.00005 kg/m² (не 0.005!) — калибриран по реални LWP стойности
    # от модела: плитка радиационна мъгла (DZ_EFF=8m нощем) дава LWP от
    # порядъка 0.0001-0.00015 kg/m² дори при солидна мъгла (VIS<600m,
    # LBPD 2025-02-25) — старият праг 0.005 беше ~40-100x над реалния
    # мащаб и никога не се задействаше, оставяйки eps_a да следва само
    # ICON облачността дори при собствена мъгла на модела.
    is_fog = lwp_col > 0.00005
    if is_fog:
        eps_a = 1.0
    else:
        w_pw  = 46.5 * (e_hPa / T_air0)
        eps_clear = 1.0 - (1.0 + w_pw) * np.exp(-np.sqrt(1.2 + 3.0 * w_pw))
        cf_c  = min(max(float(cf), 0.0), 1.0)
        eps_a = cf_c + (1.0 - cf_c) * eps_clear
        eps_a = float(min(max(eps_a, 0.60), 1.0))
    # LW_down от екранната температура — Prata/C&D са калибрирани така.
    T_rad_down = T_air0

    # 3. Радиационен баланс
    LW_down = eps_a * sigma * T_rad_down**4
    LW_up   = EPS_SFC * sigma * T_skin**4
    R_net   = LW_down - LW_up + (1.0 - ALBEDO) * sw_down

    # 4. Сензибилен поток (+: от повърхността към въздуха)
    U_eff = max(U0, U_MIN)
    H = rho0 * cp * C_H_BULK * U_eff * (T_skin - T_air0)

    # 5. Почвен флукс (+: от почвата към повърхността)
    G = LAMBDA_G * (T_soil - T_skin) / D_SOIL_G

    # 6. Роса: кондензация върху повърхността при T_skin < Td
    es_skin   = sat_vapor_pressure(np.array([T_skin]))[0]
    qsat_skin = eps_r * es_skin / (p0 - es_skin)
    E_dew = max(rho0 * C_H_BULK * U_eff * (qv0 - qsat_skin), 0.0)
    LE    = Lv * E_dew

    # ── ДИАГНОСТИКА SEB (когато SEB_DEBUG=1, само на кръгъл час) ──
    if (SEB_DEBUG or _os.environ.get("SEB_DEBUG") == "1") and abs(hour_utc - round(hour_utc)) < 0.009:
        _dTs = dt * (R_net - H + G + LE) / C_SKIN
        print(f"    SEB {hour_utc:4.1f}h sw={sw_down:6.1f} "
              f"Rnet={R_net:+7.1f} negH={-H:+7.1f} G={G:+7.1f} LE={LE:+6.1f} "
              f"| dTskin={_dTs:+6.3f}K/step "
              f"Tskin={T_skin-273.15:+6.2f} Tair={T_air0-273.15:+6.2f} "
              f"Tsoil={T_soil-273.15:+6.2f} eps_a={eps_a:.3f} U={U_eff:.2f} "
              f"cf={cf:.2f} LWP={lwp_col:.5f}kg/m2 is_fog={is_fog}", flush=True)
    # ────────────────────────────────────────────────

    # 7. Прогностично уравнение
    dT_skin = dt * (R_net - H + G + LE) / C_SKIN
    dT_skin = float(np.clip(dT_skin, -2.0, 2.0))   # единствена защита

    # Force-Restore T_soil — бавно охлаждане нощем (max 0.5 K/hr)
    dT_soil = float(np.clip(-G * dt / C_SOIL_LAYER, -0.2*dt/3600., 0.2*dt/3600.))

    return T_skin + dT_skin, H, E_dew, T_soil + dT_soil



# ──────────────────────────────────────────────────────────────────────────────
# TKE 1.5-order схема (Mellor-Yamada level 2.5, опростена)
# Литература: Bougeault & Lacarrere (1989), Duynkerke (1991)
# ──────────────────────────────────────────────────────────────────────────────

# TKE константи
Cm   = 0.556    # коефициент за Km = Cm * l * sqrt(e)
Ch   = 0.478    # коефициент за Kh (топлина/влага)
Ce   = 0.202    # дисипационна константа
e_min = 1e-5    # минимална TKE [m²/s²]
l_inf = 150.0   # асимптотична смесваща дължина [m]


def tke_mixing_length(z: np.ndarray) -> np.ndarray:
    """Смесваща дължина по Blackadar."""
    kv = 0.4
    z_safe = np.maximum(z, 0.01)
    return kv * z_safe / (1.0 + kv * z_safe / l_inf)


def tke_step(e: np.ndarray,
             theta_v: np.ndarray,
             u: np.ndarray, v: np.ndarray,
             z: np.ndarray, rho: np.ndarray,
             dt: float) -> tuple:
    """
    Прогностична стъпка за TKE e(z,t) [m²/s²].

    ∂e/∂t = P_shear + P_buoy - ε + дифузия

    Връща: (e_new, Km, Kh)
    """
    nz = len(z)
    dz = np.gradient(z)
    l  = tke_mixing_length(z)
    sq_e = np.sqrt(np.maximum(e, e_min))

    # Km и Kh от TKE
    Km = Cm * l * sq_e
    Kh = Ch * l * sq_e

    # Shear производство P_s = Km * S²
    du = np.gradient(u, z)
    dv = np.gradient(v, z)
    S2 = du**2 + dv**2
    P_shear = Km * S2

    # Buoyancy производство/потискане P_b = -Kh * N²
    # N² = (g/θv) * dθv/dz
    dtheta_v = np.gradient(theta_v, z)
    N2 = (g / theta_v) * dtheta_v
    P_buoy = -Kh * N2

    # Дисипация ε = Ce * e^(3/2) / l
    eps = Ce * e * sq_e / np.maximum(l, 0.1)

    # Дифузия на TKE: ∂/∂z(K_e * ∂e/∂z)  с K_e = Km
    de_dz  = np.gradient(e, z)
    flux_e = Km * de_dz * rho
    diff_e = np.gradient(flux_e, z) / rho

    # TKE уравнение
    de_dt = P_shear + P_buoy - eps + diff_e
    e_new = e + dt * de_dt
    # Защита: NaN/Inf → reset към e_min
    e_new = np.where(np.isfinite(e_new), e_new, e_min)
    e_new = np.clip(e_new, e_min, 10.0)   # TKE в [e_min, 10] m²/s²

    # Обновяваме Km/Kh с новото e
    sq_e_new = np.sqrt(e_new)
    Km_new = np.clip(Cm * l * sq_e_new, 1e-4, 5.0)
    Kh_new = np.clip(Ch * l * sq_e_new, 1e-4, 5.0)

    return e_new, Km_new, Kh_new


def bulk_richardson_and_shear(theta_v: np.ndarray, u: np.ndarray,
                               v: np.ndarray, z: np.ndarray) -> tuple:
    """
    Bulk Richardson number и вертикален shear |dU/dz| [1/s].
    Връща: (Ri, S) — двата масива с дължина len(z).
    """
    Ri = np.zeros_like(z)
    S  = np.zeros_like(z)
    dz  = np.diff(z)
    dth = np.diff(theta_v)
    du  = np.diff(u)
    dv  = np.diff(v)
    dU2 = du**2 + dv**2 + 1e-6

    # Ri = (g/θ) · (dθ/dz) / (dU/dz)²
    Ri[1:] = (g / theta_v[:-1]) * (dth / dz) * (dz**2) / dU2
    Ri[0]  = Ri[1]

    # S = |dU/dz| [1/s]
    S[1:]  = np.sqrt(dU2) / dz
    S[0]   = S[1]

    return np.clip(Ri, -5.0, 5.0), S


# ──────────────────────────────────────────────────────────────────────────────
# Радиационна схема — Two-stream (LW + SW)
# Bott et al. (1990) PAFOG, Bergot & Guédalia (1994) COBEL
# ──────────────────────────────────────────────────────────────────────────────

K_EXT_LW   = 130.0   # m²/kg  LW extinction на мъглени капки
K_EXT_SW   = 50.0    # m²/kg  SW extinction
ALBEDO_FOG = 0.30    # отражателна способност на мъглата
EMISS_SFC  = 0.95    # излъчвателна способност на земята
ALPHA_AIR  = 0.03    # SW поглъщане на чист въздух


def _sin_elevation(hour_utc, day_of_year, lat_deg=42.7):
    """Синус на слънчевия елевационен ъгъл по Cooper (1969)."""
    phi  = np.deg2rad(lat_deg)
    decl = np.deg2rad(23.45 * np.sin(np.deg2rad(360*(284+day_of_year)/365)))
    ha   = np.deg2rad((hour_utc - 12.0) * 15.0)
    return max(float(np.sin(phi)*np.sin(decl) +
                     np.cos(phi)*np.cos(decl)*np.cos(ha)), 0.0)


def two_stream_radiation(T, ql, z, rho, hour_utc, day_of_year=1):
    """
    Two-stream радиационна схема (LW + SW). Връща dT/dt [K/s].

    LW: F↑(z) = емисия от слоевете под z, ослабена по пътя нагоре.
        F↓(z) = емисия от атмосферата над z, ослабена надолу.
        Нагряване = -d(F↑ - F↓)/dz / (ρ·cp)

    SW: Flux отгоре надолу, ослабен от мъглата.
        Загряване = -dSW/dz · absorption / (ρ·cp)
    """
    nz    = len(T)
    dz    = np.gradient(z)
    dQ_dt = np.zeros(nz)
    B     = sigma * T**4          # черно-тялова емисия [W/m²]
    lwc_vol = ql * rho            # [kg/m³]
    lwp_path = np.cumsum(lwc_vol * dz)        # LWP от дъното до ниво i
    lwp_esc  = np.cumsum((lwc_vol*dz)[::-1])[::-1]  # LWP от ниво i до върха

    # ── F↑: flux нагоре на ниво i ──
    # = емисия от земята + емисия от слоевете j < i,
    #   всяка ослабена с τ(j→i) = exp(-K * (LWP_path[i] - LWP_path[j]))
    F_up = np.zeros(nz)
    for i in range(nz):
        # Земна повърхност
        F_up[i] = EMISS_SFC * B[0] * np.exp(-K_EXT_LW * lwp_path[i])
        # Слоеве j < i
        for j in range(i):
            eps_j   = 1.0 - np.exp(-K_EXT_LW * lwc_vol[j] * dz[j])
            tau_j_i = np.exp(-K_EXT_LW * (lwp_path[i] - lwp_path[j]))
            F_up[i] += B[j] * eps_j * tau_j_i

    # ── F↓: flux надолу на ниво i ──
    # = емисия от атмосферата над i (сив слой),
    #   ослабена с τ(top→i) = exp(-K * LWP_esc[i])
    F_down = np.zeros(nz)
    for i in range(nz):
        F_down[i] = emiss_air * B[-1] * np.exp(-K_EXT_LW * lwp_esc[i])
        # Слоеве j > i
        for j in range(i+1, nz):
            eps_j   = 1.0 - np.exp(-K_EXT_LW * lwc_vol[j] * dz[j])
            tau_j_i = np.exp(-K_EXT_LW * (lwp_esc[i] - lwp_esc[j]))
            F_down[i] += B[j] * eps_j * tau_j_i

    # Нагряване от LW [K/s]: охлаждане когато нетният upward flux нараства с z
    dQ_lw = -np.gradient(F_up - F_down, z) / (rho * cp)

    # Физическо ограничение: LW охлаждане не може да надвишава
    # ~1 K/hr при мъгла и ~0.3 K/hr при ясно небе
    # Реални стойности от PAFOG верификации: макс ~0.8 K/hr в мъглата
    lwp_col = float(lwp_path[-1])
    # max_cool варира плавно с LWP
    # при ясно: 0.8 K/hr (реални decoupled тихи нощи: 0.5–1.5 K/hr);
    # при мъгла (LWP~0.05 kg/m²): ~1.2 K/hr
    max_cool_val = 0.8 + 0.4 * np.tanh(lwp_col / 0.02)   # K/hr
    max_cool = max_cool_val / 3600.0
    dQ_lw = np.maximum(dQ_lw, -max_cool)   # ограничаваме охлаждането

    dQ_dt += dQ_lw

    # ── SW flux надолу ──
    sin_el = _sin_elevation(hour_utc, day_of_year)
    if sin_el > 0.01:
        SW_top    = 1361.0 * 0.75 * sin_el
        tau_SW    = np.exp(-K_EXT_SW * lwp_esc)   # прозрачност от върха до z
        SW_dn     = SW_top * tau_SW
        fog_mask  = ql > 1e-5
        absorpt   = np.where(fog_mask, 1.0 - ALBEDO_FOG, ALPHA_AIR)
        # В мъглата: поглъщане от дивергенцията на flux-а
        dQ_dt += -np.gradient(SW_dn, z) * absorpt / (rho * cp)
        # При ясно небе: фонов SW член (водна пара поглъща в целия PBL)
        # dT/dt_SW = SW_sfc * alpha_bulk / (rho * cp * H_pbl)
        # alpha_bulk~0.1, H_pbl~1000m → ~0.3 K/hr при обед
        H_pbl = max(z[-1], 500.0)
        dQ_dt += SW_top * ALPHA_AIR * 3.0 / (rho * cp * H_pbl)

    # ── Фоново LW охлаждане при ясно небе ──────────────────────────────────────
    # При ясно небе (без мъгла) two-stream дава ~0 охлаждане защото
    # F↑ ≈ F↓ в тънката 1D колона.
    # Реалното нощно LW охлаждане е ~0.5-1 K/hr от загуба към Космоса.
    # Параметризираме го като функция от T и час (нощем е по-силно).
    # Clear-sky LW охлаждане — поето от SEB (T_skin охлажда въздуха чрез H)
    # Блокът е премахнат. Two-stream работи само за мъглен/облачен LW.

    return dQ_dt


# Backward-compatible обвивки (step() ги вика)
def longwave_cooling(T, ql, z, rho):
    return two_stream_radiation(T, ql, z, rho, hour_utc=0.0, day_of_year=1)

def solar_heating(T, ql, rho, hour_utc, day_of_year=1):
    return np.zeros_like(T)   # заменено от two_stream_radiation

# ──────────────────────────────────────────────────────────────────────────────
# Микрофизика: кондензация / изпарение
# ──────────────────────────────────────────────────────────────────────────────

def microphysics(qv: np.ndarray, ql: np.ndarray,
                 T: np.ndarray, p: np.ndarray,
                 rho: np.ndarray, dt: float):
    """
    Bulk микрофизика: кондензация / изпарение.
    Връща: dqv [kg/kg], dql [kg/kg], dT [K]
    """
    qs   = sat_mixing_ratio(T, p)
    rh   = qv / (qs + 1e-12)
    dqv  = np.zeros_like(qv)
    dql  = np.zeros_like(ql)
    dT   = np.zeros_like(T)

    # Кондензация при rh > RH_CRIT (не строго 1.0):
    # реалните CCN активират кондензация под 100% (Köhler);
    # PAFOG-клас модели ползват ~99.5%. Хистерезисът спрямо
    # прага за изпарение (0.98) предпазва от трептене.
    RH_CRIT = 0.995
    mask_cond = rh > RH_CRIT
    if mask_cond.any():
        # Количество водна пара за кондензация (излишък над RH_CRIT·qs)
        excess = (qv - RH_CRIT * qs) * mask_cond.astype(float)
        # Ограничаваме за стабилност
        dcond  = np.minimum(excess, qv * 0.5)
        dqv   -= dcond
        dql   += dcond
        dT    += (Lv / cp) * dcond   # латентно загряване

    # Изпарение (rh < 1 и има течна вода)
    mask_evap = (rh < 0.98) & (ql > 1e-7)
    if mask_evap.any():
        deficit = (qs - qv) * mask_evap.astype(float)
        devap   = np.minimum(deficit * 0.5, ql)
        dqv    += devap
        dql    -= devap
        dT     -= (Lv / cp) * devap  # охлаждане при изпарение

    return dqv, dql, dT


# ──────────────────────────────────────────────────────────────────────────────
# Утаяване (sediment)
# ──────────────────────────────────────────────────────────────────────────────

def settling_velocity(ql: np.ndarray) -> np.ndarray:
    """
    Стокесова скорост на утаяване [m/s] за облачни капки.
    Ефективният радиус се изчислява от LWC и N_d.
    """
    lwc_g = ql * 1000.0  # kg/kg → г/кг (оценка с rho≈1)
    # r_eff от N и LWC: r = (3*LWC / 4*pi*rho_w*N)^(1/3)
    lwc_vol = np.maximum(ql, 1e-12)
    r3 = 3.0 * lwc_vol / (4.0 * np.pi * rho_w * N_d)
    r_eff = np.cbrt(r3)
    r_eff = np.clip(r_eff, r_eff_min, r_eff_max)
    # Стокес: v = 2*rho_w*g*r^2 / (9*mu); mu_air ≈ 1.8e-5
    mu_air = 1.8e-5  # Pa·s
    v_s = 2.0 * rho_w * g * r_eff**2 / (9.0 * mu_air)
    return np.clip(v_s, 0.0, 0.05)   # ограничение за стабилност


def apply_settling(ql: np.ndarray, dz: float, dt: float) -> np.ndarray:
    """Прилага утаяване чрез upwind схема."""
    v_s  = settling_velocity(ql)
    flux = v_s * ql
    dql  = np.zeros_like(ql)
    # Downward flux (капките падат надолу)
    dql[1:]  += flux[:-1] / dz
    dql[:-1] -= flux[:-1] / dz
    dql[0]   -= flux[0]   / dz  # губи се на земята
    return np.clip(dql * dt, -ql, 0.0)  # само загуба от ниво


# ──────────────────────────────────────────────────────────────────────────────
# Турбулентна дифузия (Crank-Nicolson трипо-диагонална схема)
# ──────────────────────────────────────────────────────────────────────────────

def turbulent_diffusion(phi: np.ndarray, K: np.ndarray,
                        rho: np.ndarray, z: np.ndarray, dt: float) -> np.ndarray:
    """
    Имплицитна дифузия на скалара phi с коефициент K.
    Решава: phi_new = phi + dt * d/dz(K * dphi/dz)
    """
    n  = len(phi)
    dz = np.diff(z)
    dz_full = np.concatenate([[dz[0]], 0.5*(dz[:-1]+dz[1:]), [dz[-1]]])

    # Интерфейсни K-стойности
    K_int = 0.5 * (K[:-1] + K[1:])

    # Коефициенти за тридиагонална матрица
    a = np.zeros(n)   # sub-diagonal
    b = np.ones(n)    # main diagonal
    c = np.zeros(n)   # super-diagonal

    for i in range(1, n - 1):
        dzm = z[i]   - z[i-1]
        dzp = z[i+1] - z[i]
        r_m = K_int[i-1] * dt / (dzm * dz_full[i])
        r_p = K_int[i]   * dt / (dzp * dz_full[i])
        a[i] = -r_m
        b[i] = 1.0 + r_m + r_p
        c[i] = -r_p

    # Гранични условия (Neumann - нулев поток)
    b[0] = 1.0;  c[0] = 0.0
    a[-1] = 0.0; b[-1] = 1.0

    # Thomas алгоритъм
    phi_new = _thomas(a, b, c, phi.copy())
    return phi_new


def _thomas(a, b, c, d):
    """Thomas (тридиагонален) алгоритъм."""
    n = len(d)
    c_ = np.zeros(n)
    d_ = np.zeros(n)
    x  = np.zeros(n)
    c_[0] = c[0] / b[0]
    d_[0] = d[0] / b[0]
    for i in range(1, n):
        m = b[i] - a[i] * c_[i-1]
        if abs(m) < 1e-15:
            m = 1e-15
        c_[i] = c[i] / m
        d_[i] = (d[i] - a[i] * d_[i-1]) / m
    x[-1] = d_[-1]
    for i in range(n - 2, -1, -1):
        x[i] = d_[i] - c_[i] * x[i+1]
    return x


# ──────────────────────────────────────────────────────────────────────────────
# Основен клас: FogModel1D
# ──────────────────────────────────────────────────────────────────────────────

def soil_heat_flux(T_air: float, T_soil: float,
                   T_soil_deep: float, dt: float) -> tuple:
    """
    Force-Restore схема за топлинен флукс от почвата (Deardorff 1978).

    Две прогностични T на почвата:
      T_soil      — повърхностен слой (~10cm), бързо реагира
      T_soil_deep — дълбок слой (~1m), дневна инерция

    Флукс към въздуха [W/m²]: G = Lambda * (T_soil - T_air) / d_soil
    Положителен = топлина от почвата към въздуха (нощем)
    Отрицателен = охлаждане на въздуха от студена почва (зима)

    Връща: (G, T_soil_new, T_soil_deep_new)
    """
    # Топлинен флукс от повърхностния слой към въздуха
    G = Lambda * (T_soil - T_air) / d_soil   # W/m²

    # Force-Restore уравнения
    # dT_soil/dt = -G/C_soil_layer - omega*(T_soil - T_soil_deep)
    C_layer = C_soil * d_soil   # J/m²/K

    dT_soil  = dt * (-G / C_layer - omega_d * (T_soil - T_soil_deep))
    # Дълбокият слой — бавна дневна вълна
    dT_deep  = dt * (-omega_d / (2*np.pi) * (T_soil_deep - T_soil))

    T_soil_new      = T_soil      + dT_soil
    T_soil_deep_new = T_soil_deep + dT_deep

    return G, T_soil_new, T_soil_deep_new


class FogModel1D:
    """
    Едномерен физически модел за мъгла, базиран на PAFOG.

    Параметри
    ----------
    z       : np.ndarray  - нива [m AGL], нарастващо
    T       : np.ndarray  - температура [K]
    qv      : np.ndarray  - специфична влажност [kg/kg]
    p       : np.ndarray  - налягане [Pa]
    u, v    : np.ndarray  - компоненти на вятъра [m/s]
    hour0   : float       - начален час UTC (за радиацията)
    dt      : float       - времева стъпка [s] (default 60)
    """

    def __init__(self, z, T, qv, p, u, v, hour0=0.0, dt=60.0, day_of_year=None):
        # Вертикална мрежа
        self.z    = np.array(z,  dtype=float)
        self.nz   = len(z)
        self.dz   = np.mean(np.diff(z))

        # Прогностични променливи
        self.T    = np.array(T,  dtype=float)
        self.qv   = np.array(qv, dtype=float)
        self.ql   = np.zeros(self.nz)   # начален LWC = 0
        self.u    = np.array(u,  dtype=float)
        self.v    = np.array(v,  dtype=float)
        self.p    = np.array(p,  dtype=float)

        # Производни величини
        self.rho  = self.p / (Rd * self.T)

        self.hour0 = float(hour0)
        self.dt    = float(dt)
        self.time  = 0.0   # секунди от началото

        # Ден от годината за коректна слънчева деклинация
        if day_of_year is None:
            from datetime import datetime, timezone
            self.day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
        else:
            self.day_of_year = int(day_of_year)

        # Почвена температура (от ICON, константа)
        self.T_soil = float(T[0])    # ще се презапише от run_case/run_operational
        # SEB: температура на повърхността
        self.T_skin = float(T[0]) - 1.0

        # TKE — инициализираме от равновесно производство при началния shear
        # e_init = (Cm * l * S)² / Ce  — равновесно TKE без плавучест
        _l_init = tke_mixing_length(self.z)
        _Ri_init, _S_init = bulk_richardson_and_shear(
            virtual_potential_temp(T_to_theta(np.array(T), np.array(p)), np.array(qv), np.zeros_like(np.array(qv))),
            np.array(u), np.array(v), self.z)
        self.e = np.maximum((Cm * _l_init * np.maximum(_S_init, 0.01))**2 / Ce, e_min)

        # Диагностика
        self.history  = []     # списък с dict за всеки изходен час
        self._log_qv  = False  # активира се от run_case при нужда

    # ─────────────────────────────────────────────
    # Стъпка напред
    # ─────────────────────────────────────────────

    def step(self):
        """Напредва с dt секунди."""
        T  = self.T.copy()
        qv = self.qv.copy()
        ql = self.ql.copy()

        # 1. Потенциална температура
        theta    = T_to_theta(T, self.p)
        theta_v  = virtual_potential_temp(theta, qv, ql)

        # 2. Турбуленция
        # TKE схема + Louis фонов минимум за стабилност при инверсия
        self.e, Km_tke, Kh_tke = tke_step(
            self.e, theta_v, self.u, self.v, self.z, self.rho, self.dt)

        # Louis минимален Kh при силна инверсия — предотвратява замръзване на T
        Ri, S = bulk_richardson_and_shear(theta_v, self.u, self.v, self.z)
        K_louis = louis_stability_function(Ri, self.z, S)

        # Взимаме максимума: TKE или Louis минимум
        # TKE управлява при активна турбуленция
        # Louis осигурява минимален поток при тиха инверсия
        Km = np.maximum(Km_tke, K_louis * 0.1)   # 10% от Louis като минимум
        Kh = np.maximum(Kh_tke, K_louis * 0.1)

        # 3. Дифузия (T и qv с Kh, ql с Km)
        T_new  = turbulent_diffusion(T,  Kh, self.rho, self.z, self.dt)
        qv_new = turbulent_diffusion(qv, Kh, self.rho, self.z, self.dt)
        ql_new = turbulent_diffusion(ql, Km, self.rho, self.z, self.dt)
        ql_new = np.maximum(ql_new, 0.0)

        # Защита срещу NaN от TKE нестабилност
        if not np.all(np.isfinite(T_new)):
            T_new = np.where(np.isfinite(T_new), T_new, T)
        if not np.all(np.isfinite(qv_new)):
            qv_new = np.where(np.isfinite(qv_new), qv_new, qv)

        # 4. Радиация
        hour_now = (self.hour0 + self.time / 3600.0) % 24.0
        dT_rad = two_stream_radiation(
            T_new, ql_new, self.z, self.rho, hour_now, self.day_of_year)

        # К2 resolved: cap премахнат.
        # TKE+Louis хибрид осигурява физическото ограничение на охлаждането.
        # Soil flux (при G<0) допълнително стабилизира при студена почва.
        T_new += dT_rad * self.dt

        # 5. Surface Energy Balance (SEB) — Fable5 дизайн
        sw_down_seb = solar_sw_down(hour_now, self.day_of_year)
        lwp_col_seb = float(np.sum(ql_new * self.rho * np.gradient(self.z)))

        # Облачност за LW_down. Предпочитаме (lo,mid,hi) тройки
        # (model.cc_series): ICON "ниска облачност" при НАШАТА приземна
        # RH>95% е почти сигурно самата мъгла, която гоним — дисконт ×0.2,
        # иначе прогнозата за мъглата убива мъглообразуването.
        # Средни/високи облаци топлят винаги (легитимни).
        cf_now = 0.0
        _ccs = getattr(self, "cc_series", None)
        if _ccs is not None and len(_ccs) > 0:
            _ci = min(int(self.time // 3600.0), len(_ccs) - 1)
            _row = _ccs[_ci]; _lo, _mi, _hi = _row[0], _row[1], _row[2]; _rh2i = _row[3] if len(_row)>3 else 1.0; _pr = _row[4] if len(_row)>4 else 0.0
            _es0 = sat_vapor_pressure(np.array([float(T_new[0])]))[0]
            _qs0 = eps_r * _es0 / (float(self.p[0]) - _es0)
            _rh0 = float(qv_new[0]) / max(_qs0, 1e-9)
            if _pr >= 0.1:
                pass  # дъжд → реален облак, без дисконт
            elif _rh0 > 0.95:
                _lo = _lo * 0.2
            cf_now = 1.0 - (1.0 - _lo) * (1.0 - 0.7 * _mi) * (1.0 - 0.25 * _hi)
            if _pr >= 0.1:
                cf_now = max(cf_now, 0.8)
            cf_now = min(max(cf_now, 0.0), 1.0)
        else:
            _cfs = getattr(self, "cf_series", None)
            if _cfs is not None and len(_cfs) > 0:
                _ci = min(int(self.time // 3600.0), len(_cfs) - 1)
                cf_now = float(_cfs[_ci])

        self.T_skin, H_sfc, E_dew, self.T_soil = seb_step(
            self.T_skin, self.T_soil,
            float(T_new[0]), float(qv_new[0]),
            float(self.p[0]), float(self.rho[0]),
            float(np.hypot(self.u[0], self.v[0])),
            lwp_col_seb, sw_down_seb, self.dt, hour_now,
            None, cf_now)

        # Обратна връзка: H загрява/охлажда въздуха в ефективен слой
        # Денем SW → PBL смесване в дебел слой; нощем/мъгла — тънък
        sin_el_seb = _sin_elevation(hour_now, self.day_of_year)
        if sin_el_seb > 0.1:          # ден — SW загрява PBL
            DZ_EFF_SEB = 500.0
        elif lwp_col_seb > 0.00005:     # мъгла нощем (същия праг като is_fog)
            DZ_EFF_SEB = 50.0
        else:                          # ясна нощ
            # Плитък decoupled слой: реално изстиват първите метри,
            # не 20m. С H~1.4 W/m²: 20m → 0.2 K/hr; 8m → ~0.5 K/hr,
            # близо до наблюдаваните 0.8–1 K/hr в тихи ясни нощи.
            DZ_EFF_SEB = 8.0
        T_new[0] += H_sfc * self.dt / (self.rho[0] * cp * DZ_EFF_SEB)

        # Роса: изважда влага от приземното ниво
        qv_new[0] -= E_dew * self.dt / (self.rho[0] * DZ_EFF_SEB)
        qv_new[0] = max(qv_new[0], 1e-8)

        # Saturated surface condition (Teixeira & Miranda 1999)
        # Активира се САМО при реална роса: T_skin < Td на въздуха
        # Td на въздуха от qv[0]: Td = 243.5*ln(e/6.112) / (17.67-ln(e/6.112))
        _e_air    = qv_new[0] * self.p[0] / (eps_r + qv_new[0])  # Pa
        _e_hPa    = max(_e_air / 100., 0.01)
        _ln_e     = np.log(_e_hPa / 6.112)
        _Td_air   = 243.5 * _ln_e / (17.67 - _ln_e) + 273.15  # K
        _es_skin  = sat_vapor_pressure(np.array([self.T_skin]))[0]
        _qsat_sfc = eps_r * _es_skin / (self.p[0] - _es_skin)
        # Роса: T_skin < Td_air → повърхността е под точката на оросяване
        if self.T_skin < _Td_air and qv_new[0] < _qsat_sfc:
            qv_new[0] = _qsat_sfc

        # 6. Микрофизика
        dqv, dql, dT_mic = microphysics(qv_new, ql_new, T_new, self.p, self.rho, self.dt)
        qv_new += dqv
        ql_new += dql
        T_new  += dT_mic

        # 6. Утаяване
        dql_set = apply_settling(ql_new, self.dz, self.dt)
        ql_new  = np.maximum(ql_new + dql_set, 0.0)

        # 7. Обновяване на плътността
        self.rho = self.p / (Rd * T_new)

        # Запазваме
        self.T  = T_new
        self.qv = np.maximum(qv_new, 1e-8)
        self.ql = ql_new
        self.time += self.dt

    # ─────────────────────────────────────────────
    # Запис на диагностика
    # ─────────────────────────────────────────────

    def diagnose(self):
        """Изчислява и записва текущата диагностика."""
        rh   = relative_humidity(self.qv, self.T, self.p)
        vis  = lwc_to_visibility(self.ql)
        hour = (self.hour0 + self.time / 3600.0) % 24.0

        rec = {
            "time_h"  : round(self.time / 3600.0, 2),
            "hour_utc": round(hour, 1),
            "z"       : self.z.copy(),
            "T"       : self.T.copy(),
            "qv"      : self.qv.copy(),
            "ql"      : self.ql.copy(),
            "rh"      : rh,
            "vis"     : vis,
            # Приземна (z[0])
            "T_sfc"   : float(self.T[0]),
            "rh_sfc"  : float(rh[0]),
            "vis_sfc" : float(vis[0]),
            "ql_sfc"  : float(self.ql[0]),
            "cat"     : vis_to_metar_category(float(vis[0])),
        }
        self.history.append(rec)
        return rec

    # ─────────────────────────────────────────────
    # Основна прогноза
    # ─────────────────────────────────────────────

    def run(self, hours: float = 12.0, output_interval_min: int = 60,
            verbose: bool = True):
        """
        Стартира прогнозата.

        hours               : продължителност [h]
        output_interval_min : интервал за изход [min]
        """
        steps_total   = int(hours * 3600 / self.dt)
        output_every  = int(output_interval_min * 60 / self.dt)

        self.diagnose()   # t=0

        if verbose:
            print(f"{'Час UTC':>8} | {'T[0]°C':>8} | {'RH[0]%':>7} | "
                  f"{'LWC g/m³':>9} | {'VIS m':>7} | CAT")
            print("-" * 62)
            r = self.history[0]
            print(f"{r['hour_utc']:8.1f} | {r['T_sfc']-273.15:8.2f} | "
                  f"{r['rh_sfc']*100:7.1f} | {r['ql_sfc']*1000:9.4f} | "
                  f"{r['vis_sfc']:7.0f} | {r['cat']}")

        for step_n in range(1, steps_total + 1):
            self.step()

            if step_n % output_every == 0:
                r = self.diagnose()
                if verbose:
                    print(f"{r['hour_utc']:8.1f} | {r['T_sfc']-273.15:8.2f} | "
                          f"{r['rh_sfc']*100:7.1f} | {r['ql_sfc']*1000:9.4f} | "
                          f"{r['vis_sfc']:7.0f} | {r['cat']}")

        if verbose:
            print("\nПрогнозата завърши.")
        return self.history
