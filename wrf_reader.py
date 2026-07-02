"""
wrf_reader.py
=============
Четене и извличане на вертикален профил от WRF NetCDF изход
за точката на LBSF Sofia Airport (φ=42.697°N, λ=23.406°E).

Поддържа стандартни WRF wrfout_d0X файлове.
Ако NetCDF липсва, генерира синтетичен профил за тестване.
"""

import numpy as np
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Константи за WRF
# ──────────────────────────────────────────────────────────────────────────────
LBSF_LAT = 42.697
LBSF_LON = 23.406
LBSF_ELEV = 531.0   # m AMSL

# WRF базово налягане (PB) + пертурбация (P) → реално налягане
# WRF геопотенциал: PH + PHB → Z = (PH+PHB)/g

Rd   = 287.05
cp   = 1005.0
g    = 9.81
kappa = Rd / cp
T_base = 300.0   # K (WRF T е θ' спрямо 300K)

# ──────────────────────────────────────────────────────────────────────────────
# Намиране на най-близката мрежова точка
# ──────────────────────────────────────────────────────────────────────────────

def find_nearest_ij(lat2d: np.ndarray, lon2d: np.ndarray,
                    target_lat: float, target_lon: float):
    """Връща (i, j) на най-близката точка в WRF мрежата."""
    dist2 = (lat2d - target_lat)**2 + (lon2d - target_lon)**2
    idx   = np.unravel_index(np.argmin(dist2), dist2.shape)
    return idx


# ──────────────────────────────────────────────────────────────────────────────
# Основен четец
# ──────────────────────────────────────────────────────────────────────────────

def read_wrf_profile(nc_path: str, target_time_idx: int = 0,
                     max_levels: int = 50, max_z_m: float = 3000.0):
    """
    Чете WRF wrfout файл и извлича вертикален профил за LBSF.

    Параметри
    ----------
    nc_path       : пълен път до wrfout_d0X_YYYY-MM-DD_HH:MM:SS
    target_time_idx : индекс на времето в NetCDF (default=0)
    max_levels    : максимален брой вертикални нива
    max_z_m       : горна граница на профила [m AGL]

    Върнат dict
    -----------
    {
        'z'     : np.ndarray  m AGL
        'T'     : np.ndarray  K
        'qv'    : np.ndarray  kg/kg
        'p'     : np.ndarray  Pa
        'u'     : np.ndarray  m/s
        'v'     : np.ndarray  m/s
        'hour0' : float       UTC
        'valid_time': str
    }
    """
    try:
        from netCDF4 import Dataset
        return _read_netcdf(nc_path, target_time_idx, max_levels, max_z_m)
    except ImportError:
        print("[ВНИМАНИЕ] netCDF4 не е инсталиран. "
              "Генерира се синтетичен профил за тест.")
        return synthetic_profile()
    except FileNotFoundError:
        print(f"[ВНИМАНИЕ] Файлът {nc_path} не е намерен. "
              "Генерира се синтетичен профил.")
        return synthetic_profile()


def _read_netcdf(nc_path: str, tidx: int, max_levels: int, max_z_m: float):
    """Вътрешна функция за четене на WRF NetCDF."""
    from netCDF4 import Dataset, num2date

    ds = Dataset(nc_path, 'r')

    # ── Координати ──
    lat2d = ds.variables['XLAT'][tidx, :, :]
    lon2d = ds.variables['XLONG'][tidx, :, :]
    i, j  = find_nearest_ij(lat2d, lon2d, LBSF_LAT, LBSF_LON)
    print(f"[WRF] Най-близка точка: i={i}, j={j}  "
          f"(φ={lat2d[i,j]:.3f}°N, λ={lon2d[i,j]:.3f}°E)")

    # ── Геопотенциал → Z AGL ──
    PH  = ds.variables['PH'][tidx, :, i, j]    # пертурбация
    PHB = ds.variables['PHB'][tidx, :, i, j]   # базово
    z_w = (PH + PHB) / g                        # нива на масите (staggered)
    # Дестагиране: средно между съседни нива на стените
    z_m = 0.5 * (z_w[:-1] + z_w[1:])           # нива на маси [m AMSL]
    z_agl = z_m - z_m[0] + 2.0                  # AGL (2 m за приземния слой)

    # ── Налягане ──
    P  = ds.variables['P'][tidx, :, i, j]
    PB = ds.variables['PB'][tidx, :, i, j]
    p  = P + PB   # Pa

    # ── Температура: WRF T е θ' спрямо 300 K ──
    T_pert = ds.variables['T'][tidx, :, i, j]
    theta  = T_pert + T_base   # K (потенциална)
    T      = theta * (p / 1e5) ** kappa  # K (абсолютна)

    # ── Влажност ──
    qv = ds.variables['QVAPOR'][tidx, :, i, j]   # kg/kg

    # ── Вятър (U, V са staggered) ──
    U_stag = ds.variables['U'][tidx, :, i, j]
    V_stag = ds.variables['V'][tidx, :, i, j]
    # Дестагиране (опростено — приемаме стойността директно)
    u = U_stag
    v = V_stag

    # ── Валиден час ──
    times    = ds.variables['Times'][tidx]
    try:
        ts_str   = b''.join(times).decode('utf-8')
    except Exception:
        ts_str   = ''.join(times)
    # Формат: 2024-11-15_06:00:00
    ts_str   = ts_str.replace('_', ' ')
    try:
        dt_valid = datetime.strptime(ts_str.strip(), '%Y-%m-%d %H:%M:%S')
        hour0    = dt_valid.hour + dt_valid.minute / 60.0
    except ValueError:
        hour0 = 0.0
        ts_str = "неизвестно"

    ds.close()

    # ── Филтриране до max_z_m и max_levels ──
    mask = z_agl <= max_z_m
    z_agl = z_agl[mask][:max_levels]
    T     = T[mask][:max_levels]
    qv    = np.maximum(qv[mask][:max_levels], 1e-8)
    p     = p[mask][:max_levels]
    u     = u[mask][:max_levels]
    v     = v[mask][:max_levels]

    print(f"[WRF] Извлечени {len(z_agl)} нива  (0–{z_agl[-1]:.0f} m AGL)")
    print(f"[WRF] Валиден час: {ts_str} UTC  →  hour0={hour0:.1f}")
    print(f"[WRF] T[0]={T[0]-273.15:.1f}°C  qv[0]={qv[0]*1000:.2f} g/kg")

    return {
        'z'          : z_agl,
        'T'          : T,
        'qv'         : qv,
        'p'          : p,
        'u'          : u,
        'v'          : v,
        'hour0'      : hour0,
        'valid_time' : ts_str,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Синтетичен профил (за тест без WRF файл)
# ──────────────────────────────────────────────────────────────────────────────

def synthetic_profile(hour0: float = 20.0, sfc_T_C: float = 4.0,
                      sfc_rh: float = 0.92, nz: int = 40,
                      z_top: float = 2000.0):
    """
    Генерира реалистичен зимен вечерен профил за LBSF —
    типична ситуация преди радиационна мъгла.

    Параметри
    ----------
    hour0   : начален UTC час
    sfc_T_C : приземна температура [°C]
    sfc_rh  : приземна относителна влажност [0..1]
    nz      : брой нива
    z_top   : горна граница [m AGL]
    """
    from fog_model import sat_vapor_pressure, sat_mixing_ratio, eps_r

    print(f"\n[СИНТЕТИЧЕН ПРОФИЛ]  T_sfc={sfc_T_C}°C  RH={sfc_rh*100:.0f}%  "
          f"hour0={hour0:.0f}UTC")

    z   = np.linspace(2.0, z_top, nz)
    T_C = np.full(nz, sfc_T_C)

    # Типичен зимен профил на LBSF:
    # - приземна инверсия ~0–200 m (+1.5 K/100m)
    # - неутрален слой 200–800 m (-0.6 K/100m)
    # - конвективен слой нагоре
    for k, zk in enumerate(z):
        if zk < 200:
            T_C[k] = sfc_T_C + 1.5 * (zk / 100.0)       # инверсия
        elif zk < 800:
            T_C[k] = (sfc_T_C + 3.0) - 0.6 * ((zk - 200) / 100.0)
        else:
            T_C[k] = (sfc_T_C + 3.0 - 0.6*6.0) - 0.65 * ((zk - 800) / 100.0)

    T_K = T_C + 273.15

    # Налягане по хидростатичен закон (p0=1013 hPa на елевацията на LBSF)
    p0  = 95000.0  # Pa (≈ p на 531 m AMSL)
    rho0 = p0 / (287.05 * T_K[0])
    p   = p0 * np.exp(-z / (287.05 * T_K / 9.81))

    # Влажност: RH намалява с височина
    rh_profile = sfc_rh * np.exp(-z / 600.0) + 0.4 * (1 - np.exp(-z / 600.0))
    rh_profile = np.clip(rh_profile, 0.3, 0.99)
    es  = sat_vapor_pressure(T_K)
    qv  = eps_r * rh_profile * es / (p - rh_profile * es)
    qv  = np.maximum(qv, 1e-8)

    # Слаб вятър (2 m/s от SE — типично за мъглен ден в София)
    u   = -1.4 * np.ones(nz)   # западна компонента
    v   =  1.4 * np.ones(nz)   # южна компонента

    return {
        'z'          : z,
        'T'          : T_K,
        'qv'         : qv,
        'p'          : p,
        'u'          : u,
        'v'          : v,
        'hour0'      : hour0,
        'valid_time' : f'synthetic_{hour0:02.0f}UTC',
    }
