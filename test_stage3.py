# -*- coding: utf-8 -*-
"""
test_stage3.py — поведенчески тестове за Етап 3 (тройката)
================================================================
Изпълними БЕЗ ICON кеш и без мрежа. Пускат се ПРЕДИ гейта.

    python test_c2.py

Обхватът на Етап 2′ е САМО прогностичният импулс. Дирихле условието за
скаларите и DZ_EFF остават непокътнати — те бяха в Етап 2 и той падна.

C1  скаларният път Е НЕПРОМЕНЕН   (регресионен предпазител)
C10 обменната скорост е в мишената при реалистичен вятър
C11 прагът на кондензация действа и е по летище
C2  старото поведение е частен случай
C3  u[0] намира равновесие, не нула и не трептене
C4  равновесието е в разумно отношение към лог-профила
C5  при 8 m/s decoupling няма
C6  Kh пада с вятъра — целта на етапа
C7  без хистерезис при ±5 % смущение
C8  мъглата оцелява при наситен старт   ← това уби Етап 2
C9  диагностичните пътеки не падат
"""
import sys, os, io, contextlib
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fog_model as fm
from fog_model import (FogModel1D, turbulent_diffusion, tke_step,
                       lwc_to_visibility, sat_vapor_pressure,
                       T_to_theta, virtual_potential_temp, C_D_MOM)

OK = [True]


def check(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'ПАДА'} {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        OK[0] = False


def grid():
    return np.concatenate([np.logspace(np.log10(0.5), np.log10(50), 20),
                           np.linspace(55, 2000, 20)])


def base(U10=3.0, inv=6.0):
    z = grid()
    p = 1e5 * np.exp(-z / 8400.0)
    T = 283.0 - 0.0065 * z + inv * (1.0 - np.exp(-z / 100.0))
    qv = np.full_like(z, 0.004)
    rho = p / (287.0 * T)
    u = U10 * np.log(np.maximum(z, 0.05) / 0.05) / np.log(10.0 / 0.05)
    return z, p, T, qv, rho, u, np.zeros_like(z)


def spin(U10=3.0, inv=6.0, n=300, pert=0.0):
    z, p, T, qv, rho, u, v = base(U10, inv)
    u = u * (1.0 + pert)
    m = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=15)
    th = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
    Kh = None
    for _ in range(n):
        m.e, Km, Kh = tke_step(m.e, th, m.u, m.v, m.z, m.rho, m.dt)
        m.u, m.v = m.momentum_step(Km)
    return m, float(Kh[1])


# ══════════════════════════════════════════════════════════════
print("\nC1  скаларният път е НЕПРОМЕНЕН (Дирихле остава)")
z, p, T, qv, rho, u, v = base()
phi = np.full_like(z, 280.0); phi[0] = 270.0
out = phi.copy()
for _ in range(60):
    out = turbulent_diffusion(out, np.full_like(z, 1.0), rho, z, 60.0)
check("ниво 0 остава заковано", abs(out[0] - phi[0]) < 1e-9,
      f"{phi[0]:.3f} → {out[0]:.6f}")
import inspect
src = inspect.getsource(turbulent_diffusion)
check("няма flux_bot аргумент", "flux_bot" not in src)
check("Дирихле редът е налице", "b[0] = 1.0;  c[0] = 0.0" in src)

# ══════════════════════════════════════════════════════════════
print("\nC2  старото поведение е частен случай")
z, p, T, qv, rho, u, v = base()
m = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=15)
u0 = m.u.copy()
_cd = fm.C_D_MOM
fm.C_D_MOM = 0.0
try:
    for _ in range(60):
        m.u, m.v = m.momentum_step(np.zeros_like(z))
finally:
    fm.C_D_MOM = _cd
check("Km=0 и C_D=0 → импулсът не се мени",
      np.allclose(m.u, u0, atol=1e-12),
      f"max|Δu| = {np.max(np.abs(m.u - u0)):.2e}")

# ══════════════════════════════════════════════════════════════
print("\nC3  u[0] намира равновесие")
z, p, T, qv, rho, u, v = base(U10=3.0)
m = FogModel1D(z, T, qv, p, u, v, hour0=18.0, dt=60.0, day_of_year=15)
th = virtual_potential_temp(T_to_theta(T, p), qv, np.zeros_like(qv))
hist = []
for k in range(360):
    m.e, Km, Kh = tke_step(m.e, th, m.u, m.v, m.z, m.rho, m.dt)
    m.u, m.v = m.momentum_step(Km)
    if k % 60 == 59:
        hist.append(float(m.u[0]))
print(f"    u[0] по часове: " + "  ".join(f"{x:.3f}" for x in hist))
d = [hist[i] - hist[i+1] for i in range(len(hist)-1)]
rel = abs(hist[-1] - hist[-2]) / max(hist[-1], 1e-9)
check("не стига нула", hist[-1] > 0.05, f"{hist[-1]:.4f} m/s")
# Критерият е СХОДИМОСТ, не абсолютен праг: промяната за последния час
# да е под 5 % от стойността, и стъпките да намаляват.
check("квазистационарно (<5 %/h накрая)", rel < 0.05, f"{rel:.1%}")
check("стъпките намаляват", d[-1] < d[0] / 2.0,
      f"първа {d[0]:.3f} → последна {d[-1]:.3f}")
check("без трептене", all(x > -1e-6 for x in d),
      f"мин. стъпка {min(d):+.4f}")

# ══════════════════════════════════════════════════════════════
print("\nC4  равновесието е в разумно отношение към лог-профила")
for U10 in (1.0, 3.0, 8.0):
    mm, _ = spin(U10=U10, n=300)
    z0 = 0.05
    u_log = U10 * np.log(mm.z[0] / z0) / np.log(10.0 / z0)
    ratio = float(mm.u[0]) / u_log
    print(f"    U10={U10:>4.1f}  лог={u_log:.3f}  модел={float(mm.u[0]):.3f}  "
          f"отношение={ratio:.2f}")
    check(f"U10={U10}: 0.3 < отношение < 1.5", 0.3 < ratio < 1.5, f"{ratio:.2f}")

# ══════════════════════════════════════════════════════════════
print("\nC5  при 8 m/s decoupling няма")
m8, kh8 = spin(U10=8.0, n=300)
check("вятърът остава осезаем", float(m8.u[0]) > 1.0, f"u[0]={float(m8.u[0]):.3f}")
check("турбуленцията е жива", kh8 > 1e-3, f"Kh[1]={kh8:.5f}")

# ══════════════════════════════════════════════════════════════
print("\nC6  Kh пада с вятъра — целта на етапа")
res = {}
for U10 in (8.0, 3.0, 1.0, 0.5):
    mm, kh = spin(U10=U10, n=300)
    res[U10] = (float(mm.u[0]), kh)
    print(f"    U10={U10:>4.1f}  u[0]={res[U10][0]:.4f}  Kh[1]={kh:.6f}")
ratio = res[8.0][1] / max(res[0.5][1], 1e-12)
check("Kh пада с намаляване на вятъра", res[0.5][1] < res[8.0][1])
check("отношението е поне 10×", ratio > 10.0, f"{ratio:.1f}×")

# ══════════════════════════════════════════════════════════════
print("\nC7  без хистерезис при ±5 % смущение")
worst = 0.0
print(f"    {'U10':>5} {'−5%':>11} {'номинал':>11} {'+5%':>11} {'разсейване':>11}")
for U10 in (0.8, 1.2, 2.0, 3.0):
    a = spin(U10=U10, pert=-0.05)[1]
    b = spin(U10=U10, pert=0.0)[1]
    c = spin(U10=U10, pert=+0.05)[1]
    sp = (max(a, b, c) - min(a, b, c)) / max(b, 1e-12)
    worst = max(worst, sp)
    print(f"    {U10:>5.1f} {a:>11.6f} {b:>11.6f} {c:>11.6f} {sp:>10.1%}")
check("гладка реакция (<25 %)", worst < 0.25, f"най-лошо {worst:.1%}")

# ══════════════════════════════════════════════════════════════
print("\nC8  мъглата оцелява при наситен старт")
# Репликира LBGO_CFOG_2024-12-30: T=Td=0 °C, топла ICON почва, облачност
z = grid()
p = 1e5 * np.exp(-z / 8400.0)
T = 273.15 + 2.2 * np.minimum(z, 150.0) / 100.0
es = sat_vapor_pressure(T)
qv = 0.622 * es / (p - es) * np.clip(1.0 - z / 3000.0, 0.4, 1.0)
mf = FogModel1D(z, T, qv, p, np.full_like(z, 1.0), np.zeros_like(z),
                hour0=18.0, dt=60.0, day_of_year=365)
mf.T_soil = 273.65
mf.T_skin = min(mf.T_soil, float(T[0]))
mf.cc_series = [(c, 0.0, 0.0, 1.0, 0.0) for c in
                [.65, .69, .54, .55, .16, 0, .02, .01, 0, .01, 0, 0, .03, 0, 0]]
T0 = float(mf.T[0]); vmin = 1e9
for _ in range(14 * 60):
    mf.step()
    vmin = min(vmin, float(lwc_to_visibility(np.array([mf.ql[0]]))[0]))
print(f"    ΔT={float(mf.T[0])-T0:+.2f} K   minVIS={vmin:.0f} m   "
      f"u[0]={float(mf.u[0]):.3f} m/s")
check("мъгла се образува", vmin < 2000, f"minVIS = {vmin:.0f} m")
check("плътността е разумна", 100 < vmin < 1500, f"{vmin:.0f} m")

# ══════════════════════════════════════════════════════════════
print("\nC10  обменна скорост — мишена 0.005–0.02 m/s при жив вятър")
from fog_model import exchange_velocity, Z_REF_SEB
print(f"    {'U':>5} {'ΔT':>6} {'C_H·U':>9} {'старо':>9} {'×':>6}")
w_fog = w_fa = None
for U, dT, lbl in ((0.5, 1.0, "мъглена нощ"), (1.03, 1.59, "фалшива аларма"),
                   (0.3, 3.0, "много устойчиво"), (2.0, 1.0, "ветровито")):
    w = exchange_velocity(U, 275.0 - dT, 275.0, Z_REF_SEB)
    old = 1.2e-3 * U
    print(f"    {U:>5.2f} {dT:>6.2f} {w:>9.5f} {old:>9.5f} {w/old:>5.1f}×  {lbl}")
    if lbl == "мъглена нощ": w_fog = w
    if lbl == "фалшива аларма": w_fa = w
check("усилва обмена поне 3×", w_fog / (1.2e-3*0.5) > 3.0)
check("различава FA от мъглена нощ", w_fa > w_fog * 1.5,
      f"{w_fa/w_fog:.1f}×")
check("не надхвърля мишената", w_fa < 0.03, f"{w_fa:.5f}")
w_stab = exchange_velocity(0.3, 275.0-3.0, 275.0, Z_REF_SEB)
check("устойчивостта дроселира", w_stab < w_fog, f"{w_stab:.5f} < {w_fog:.5f}")

print("\nC11  праг на кондензация — действа и е по летище")
from fog_model import microphysics, sat_mixing_ratio
zz = np.array([0.5, 1.0]); pp = np.array([1e5, 1e5])
TT = np.array([275.0, 275.0])
qs = sat_mixing_ratio(TT, pp)
for rh_test, crit, want in ((0.985, 0.98, True), (0.985, 0.995, False),
                            (0.975, 0.97, True), (0.975, 0.98, False)):
    qvv = qs * rh_test
    dqv, dql, dTm = microphysics(qvv, np.zeros(2), TT, pp,
                                 np.array([1.2, 1.2]), 60.0, crit)
    got = dql[0] > 0
    check(f"RH={rh_test:.3f} праг={crit:.3f} → {'кондензира' if want else 'не'}",
          got == want)
print("\nC9  диагностичните пътеки не падат")
_saved = (fm.TERM_DEBUG, fm.SEB_DEBUG)
fm.TERM_DEBUG = True; fm.SEB_DEBUG = True
try:
    crashed = None
    for h0, doy, lbl in ((18.0, 365, "нощ"), (10.0, 180, "ден")):
        z, p, T, qv, rho, u, v = base(U10=1.0)
        mm = FogModel1D(z, T, qv, p, u, v, hour0=h0, dt=60.0, day_of_year=doy)
        mm.T_soil = float(T[0]); mm.T_skin = float(T[0]) - 0.5
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                for _ in range(3):
                    mm.step()
        except Exception as e:
            crashed = f"{lbl}: {type(e).__name__}: {e}"
            break
        out = buf.getvalue()
        check(f"{lbl}: TERM се печата", "TERM" in out)
        check(f"{lbl}: SEB се печата", "SEB" in out)
    check("нито един режим не пада", crashed is None, crashed or "")
finally:
    fm.TERM_DEBUG, fm.SEB_DEBUG = _saved

print("\n" + "=" * 64)
print("  ВСИЧКИ ТЕСТОВЕ МИНАХА" if OK[0] else "  ИМА ПАДНАЛИ ТЕСТОВЕ")
print("=" * 64)
sys.exit(0 if OK[0] else 1)
