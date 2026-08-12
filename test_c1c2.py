# -*- coding: utf-8 -*-
"""
test_c1c2.py — поведенчески тестове за Етап 2 (C1 + C2)
========================================================
Изпълними БЕЗ ICON кеш и без мрежа. Пускат се ПРЕДИ гейта.

    python test_c1c2.py

B1  нулев поток: колонен интеграл се запазва
B2  старото поведение е частен случай (Km→0 и без триене)
B3  u[0] затихва за 2–3 h без трептене (лог-профил 3 m/s + инверсия)
B4  при 8 m/s decoupling НЯМА
B5  TKE умира при тиха инверсия; Kh пада с порядъци
B6  бистабилност: няма хистерезис около критичния режим
B7  Дирихле вече не заковава ниво 0
"""
import sys, os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fog_model import (FogModel1D, turbulent_diffusion, tke_step,
                       T_to_theta, virtual_potential_temp,
                       C_D_MOM, U_MIN, e_min)

OK = [True]


def check(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'ПАДА'} {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        OK[0] = False


def grid(nz=40, ztop=1000.0):
    return (np.linspace(0, 1, nz) ** 2.2) * ztop


def base_state(U10=3.0, inv_K=6.0, z0=0.05):
    z   = grid()
    p   = 1e5 * np.exp(-z / 8400.0)
    T   = 283.0 - 0.0065 * z + inv_K * (1.0 - np.exp(-z / 100.0))
    qv  = np.full_like(z, 0.006)
    rho = p / (287.0 * T)
    u   = U10 * np.log(np.maximum(z, z0) / z0) / np.log(10.0 / z0)
    v   = np.zeros_like(z)
    return z, p, T, qv, rho, u, v


# ══════════════════════════════════════════════════════════════
print("\nB1  нулев поток на дъното — колонен интеграл се запазва")
z, p, T, qv, rho, u, v = base_state()
dz_full = np.concatenate([[np.diff(z)[0]],
                          0.5 * (np.diff(z)[:-1] + np.diff(z)[1:]),
                          [np.diff(z)[-1]]])
phi = np.full_like(z, 280.0)
phi[:5] = 270.0                       # смущение само долу
K = np.where(z < 200.0, 0.5, 0.0)     # K=0 горе → горната граница не влияе
I0 = float(np.sum(phi * dz_full))
out = phi.copy()
for _ in range(120):
    out = turbulent_diffusion(out, K, rho, z, 60.0)
I1 = float(np.sum(out * dz_full))
check("интеграл запазен", abs(I1 / I0 - 1.0) < 1e-10,
      f"{I1/I0:.12f}")
check("ниво 0 вече НЕ е заковано", abs(out[0] - phi[0]) > 0.1,
      f"{phi[0]:.2f} → {out[0]:.3f}")

# ══════════════════════════════════════════════════════════════
print("\nB2  старото поведение е частен случай")
# При Km≈0 и изключено триене импулсът трябва да остане непроменен
z, p, T, qv, rho, u, v = base_state()
m = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=180)
Km_zero = np.zeros_like(z)
u_before = m.u.copy()
import fog_model as fm
_cd = fm.C_D_MOM
fm.C_D_MOM = 0.0
try:
    for _ in range(60):
        m.u, m.v = m.momentum_step(Km_zero)
finally:
    fm.C_D_MOM = _cd
check("Km=0 и C_D=0 → импулсът не се мени",
      np.allclose(m.u, u_before, atol=1e-12),
      f"max|Δu| = {np.max(np.abs(m.u - u_before)):.2e}")

# ══════════════════════════════════════════════════════════════
print("\nB3  u[0] затихва плавно (лог-профил 3 m/s + инверсия)")
z, p, T, qv, rho, u, v = base_state(U10=3.0)
m = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=180)
hist = []
theta_v = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
for k in range(300):                  # 5 h
    m.e, Km, Kh = tke_step(m.e, theta_v, m.u, m.v, m.z, m.rho, m.dt)
    m.u, m.v = m.momentum_step(Km)
    if k % 60 == 59:
        hist.append(float(m.u[0]))
print(f"    u[0] по часове: " + "  ".join(f"{x:.3f}" for x in hist))
check("затихва", hist[-1] < hist[0], f"{hist[0]:.3f} → {hist[-1]:.3f}")
check("не стига нула", hist[-1] > 1e-3, f"{hist[-1]:.4f} m/s")
d = np.diff(hist)
check("монотонно, без трептене", np.all(d < 1e-9),
      f"max прираст {np.max(d):+.2e}")

# ══════════════════════════════════════════════════════════════
print("\nB4  при 8 m/s decoupling няма")
z, p, T, qv, rho, u, v = base_state(U10=8.0)
m8 = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=180)
theta_v = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
for _ in range(300):
    m8.e, Km, Kh = tke_step(m8.e, theta_v, m8.u, m8.v, m8.z, m8.rho, m8.dt)
    m8.u, m8.v = m8.momentum_step(Km)
check("вятърът остава осезаем", m8.u[0] > 0.5, f"u[0] = {m8.u[0]:.3f} m/s")
check("турбуленцията остава жива", Kh[1] > 1e-3, f"Kh[1] = {Kh[1]:.5f}")

# ══════════════════════════════════════════════════════════════
print("\nB5  TKE умира при тиха инверсия, Kh пада с порядъци")
res = {}
for U10 in (8.0, 3.0, 1.0, 0.5):
    z, p, T, qv, rho, u, v = base_state(U10=U10)
    mm = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=180)
    th = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
    for _ in range(300):
        mm.e, Km, Kh = tke_step(mm.e, th, mm.u, mm.v, mm.z, mm.rho, mm.dt)
        mm.u, mm.v = mm.momentum_step(Km)
    res[U10] = (float(mm.u[0]), float(Kh[1]), float(mm.e[1]))
    print(f"    U10={U10:>4.1f}  u[0]={res[U10][0]:.4f}  "
          f"Kh[1]={res[U10][1]:.6f}  e[1]={res[U10][2]:.2e}")
check("Kh пада с намаляване на вятъра",
      res[0.5][1] < res[8.0][1], f"{res[8.0][1]:.5f} → {res[0.5][1]:.6f}")
check("отношението е поне 10×",
      res[8.0][1] / max(res[0.5][1], 1e-12) > 10.0,
      f"{res[8.0][1]/max(res[0.5][1],1e-12):.1f}×")

# ══════════════════════════════════════════════════════════════
print("\nB6  бистабилност — има ли хистерезис около критичния режим")
def relax(U10, n=300, perturb=0.0):
    z, p, T, qv, rho, u, v = base_state(U10=U10)
    u = u * (1.0 + perturb)
    mm = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=180)
    th = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
    for _ in range(n):
        mm.e, Km, Kh = tke_step(mm.e, th, mm.u, mm.v, mm.z, mm.rho, mm.dt)
        mm.u, mm.v = mm.momentum_step(Km)
    return float(mm.u[0]), float(Kh[1])
print(f"    {'U10':>5} {'−5%':>12} {'номинал':>12} {'+5%':>12}  {'разсейване':>11}")
worst = 0.0
for U10 in (0.8, 1.2, 2.0, 3.0):
    a = relax(U10, perturb=-0.05)[1]
    b = relax(U10, perturb=0.0)[1]
    c = relax(U10, perturb=+0.05)[1]
    spread = (max(a, b, c) - min(a, b, c)) / max(b, 1e-12)
    worst = max(worst, spread)
    print(f"    {U10:>5.1f} {a:>12.6f} {b:>12.6f} {c:>12.6f}  {spread:>10.1%}")
check("реакцията е гладка (<25% при ±5% смущение)", worst < 0.25,
      f"най-лошо {worst:.1%}")

# ══════════════════════════════════════════════════════════════
print("\nB7  ниво 0 получава турбулентен поток (C1)")
z, p, T, qv, rho, u, v = base_state()
Tp = T.copy(); Tp[0] -= 8.0            # студено ниво 0
K = np.full_like(z, 0.2)
out = Tp.copy()
for _ in range(60):
    out = turbulent_diffusion(out, K, rho, z, 60.0)
check("ниво 0 се затопля отгоре", out[0] - Tp[0] > 0.5,
      f"{Tp[0]-273.15:+.2f}°C → {out[0]-273.15:+.2f}°C "
      f"(+{out[0]-Tp[0]:.2f} K за 1 h)")

print("\nB8  нощното охлаждане е във физически диапазон")
def full_night(U10, inv, summer, hours=11.0):
    z = grid(); p = 1e5 * np.exp(-z / 8400.0)
    T = (291.0 if summer else 278.0) - 0.0065 * z + inv * (1 - np.exp(-z / 100.0))
    qv = np.full_like(z, 0.0095 if summer else 0.004)
    u = U10 * np.log(np.maximum(z, 0.05) / 0.05) / np.log(10 / 0.05)
    mm = FogModel1D(z, T, qv, p, u, np.zeros_like(z), hour0=19.0, dt=60.0,
                    day_of_year=180 if summer else 15)
    mm.T_soil = float(T[0]); mm.T_skin = float(T[0]) - 1.0
    T0 = float(mm.T[0])
    for _ in range(int(hours * 60)):
        mm.step()
    return float(mm.T[0]) - T0, float(mm.u[0])

for lbl, U, inv, sm in (("лято ясно U=1", 1.0, 0.0, True),
                        ("лято ясно U=5", 5.0, 0.0, True),
                        ("зима инв. U=0.5", 0.5, 4.0, False),
                        ("зима инв. U=3", 3.0, 4.0, False)):
    dT, u0 = full_night(U, inv, sm)
    print(f"    {lbl:<18} ΔT = {dT:+6.2f} K   u[0] = {u0:.3f} m/s")
    check(f"{lbl}: охлажда", dT < -0.5, f"{dT:+.2f} K")
    check(f"{lbl}: не бяга", dT > -20.0, f"{dT:+.2f} K")

d1, _ = full_night(1.0, 0.0, True)
d5, _ = full_night(5.0, 0.0, True)
check("охлаждането реагира на вятъра", abs(d5 - d1) > 0.05,
      f"U=1: {d1:+.2f} K   U=5: {d5:+.2f} K   Δ={d5-d1:+.2f}")

print("\nB9  диагностичните пътеки не падат (TERM_DEBUG / SEB_DEBUG)")
# Крашът от 30.07: DZ_EFF_SEB = None нощем срещу f"{DZ_EFF_SEB:.0f}".
# Падаше САМО при включена диагностика, затова B1–B8 го пропуснаха.
import fog_model as _fm
_saved = (_fm.TERM_DEBUG, _fm.SEB_DEBUG)
_fm.TERM_DEBUG = True
_fm.SEB_DEBUG = True
try:
    import io as _io, contextlib as _cl
    crashed = None
    for h0, doy, lbl in ((18.0, 365, "нощ"), (10.0, 180, "ден")):
        z, p, T, qv, rho, u, v = base_state(U10=1.0)
        mm = FogModel1D(z, T, qv, p, u, v, hour0=h0, dt=60.0, day_of_year=doy)
        mm.T_soil = float(T[0]); mm.T_skin = float(T[0]) - 0.5
        buf = _io.StringIO()
        try:
            with _cl.redirect_stdout(buf):
                for _ in range(3):
                    mm.step()
        except Exception as e:
            crashed = f"{lbl}: {type(e).__name__}: {e}"
            break
        out = buf.getvalue()
        check(f"{lbl}: TERM ред се печата", "TERM" in out)
        check(f"{lbl}: SEB ред се печата", "SEB" in out)
    check("нито един режим не пада", crashed is None, crashed or "")
finally:
    _fm.TERM_DEBUG, _fm.SEB_DEBUG = _saved

print("\n" + "=" * 62)
print("  ВСИЧКИ ТЕСТОВЕ МИНАХА" if OK[0] else "  ИМА ПАДНАЛИ ТЕСТОВЕ")
print("=" * 62)
sys.exit(0 if OK[0] else 1)
