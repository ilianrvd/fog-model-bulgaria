"""
test_D1_door.py
===============
Приемателни тестове за D1 — отваряемата долна врата в
`turbulent_diffusion`. Изпълними ПРЕДИ гейта, нула пускания на модела.

    python test_D1_door.py

Тества:
  1. РЕГРЕСИЯ — при D1 изключено резултатът е байт-в-байт стария
  2. ЗАПАЗВАНЕ — при Нойман колонният интеграл се запазва (Дирихле НЕ го
     запазва: +294 % за 100 стъпки при K=0.05, измерено 27.07)
  3. ВРАТАТА РАБОТИ — долната клетка вече обменя с z[1]
  4. КЛИПЪТ ДЕЙСТВА — Kh на вратата не надхвърля тавана
  5. СТАБИЛНОСТ — без осцилации и NaN при 720 стъпки (12 часа)
  6. МАЩАБ — ΔT[0] за час е физически, не 13–28 K като при пряко
     прилагане на потока

Изходен код 0 = всички минали.
"""

import os
import sys

import numpy as np

# Тестваме и двата режима — зареждаме модула с D1 изключено и
# превключваме програмно, за да не зависи от средата.
os.environ.setdefault("D1_DOOR", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fog_model as FM


# ──────────────────────────────────────────────────────────────
# Помощни
# ──────────────────────────────────────────────────────────────

def make_column():
    """
    ТОЧНАТА мрежа от run_case.py / verify_cases.py (проверено 12.08.2026,
    двата файла са идентични тук):
        z_log = logspace(log10(0.5), log10(50), 20)
        z_lin = linspace(55, 2000, 20)
        z     = concatenate([z_log, z_lin])   # 40 нива

    ВАЖНО: предната версия на този файл ползваше geomspace(2, 2000, 34)
    — измислена мрежа, никога сверена с кода. dz[0] там излизаше 0.47 m;
    реалното е 0.137 m (3.4× по-тънко). Понеже характерното време на
    вратата е dz²/K, грешката в мрежата е замаскирала грешка от порядък
    в самия D1_KH_MAX. Физическите изводи от диагностичните пускания
    (d1_on.txt/d1_hit.txt, реален run_case.py) остават верни — засегнати
    са само числата в ТОЗИ тестов файл.
    """
    z_log = np.logspace(np.log10(0.5), np.log10(50), 20)
    z_lin = np.linspace(55, 2000, 20)
    return np.concatenate([z_log, z_lin])


def col_integral(phi, z, rho=None):
    """Колонен интеграл ∫phi·rho dz — величината, която трябва да се пази."""
    dz = np.gradient(z)
    if rho is None:
        rho = np.ones_like(z)
    return float(np.sum(phi * rho * dz))


def ok(cond, msg, detail=""):
    print(f"  {'✓' if cond else '✗'} {msg}" + (f"   {detail}" if detail else ""))
    return bool(cond)


# ──────────────────────────────────────────────────────────────
# Тестове
# ──────────────────────────────────────────────────────────────

def test_regression():
    """При D1 изключено — байт-в-байт старото поведение."""
    print("\nТЕСТ 1 — регресия: D1 изключено = старо поведение")
    z = make_column()
    rho = np.full_like(z, 1.2)
    T = 280.0 - 0.005 * z + 3.0 * np.exp(-z / 50.0)   # инверсия
    K = np.full_like(z, 0.5)

    old = FM.turbulent_diffusion(T.copy(), K, rho, z, 60.0)
    new = FM.turbulent_diffusion(T.copy(), K, rho, z, 60.0,
                                 bottom="dirichlet", k_door=None)
    same = np.array_equal(old, new)
    frozen = np.isclose(new[0], T[0], rtol=0, atol=0)
    return (ok(same, "по подразбиране = изрично dirichlet")
            and ok(frozen, "долната клетка е замразена (както преди)",
                   f"T[0]: {T[0]:.6f} → {new[0]:.6f}"))


def test_conservation():
    """Нойман пази колонния интеграл; Дирихле не го пази."""
    print("\nТЕСТ 2 — запазване на колонния интеграл")
    z = make_column()
    rho = np.ones_like(z)
    K = np.full_like(z, 0.05)
    phi0 = 10.0 + 5.0 * np.exp(-z / 100.0)

    for label, bc in (("Дирихле (старо)", "dirichlet"),
                      ("Нойман (D1)", "neumann")):
        phi = phi0.copy()
        for _ in range(100):
            phi = FM.turbulent_diffusion(phi, K, rho, z, 60.0,
                                         bottom=bc,
                                         k_door=None)
        i0 = col_integral(phi0, z)
        i1 = col_integral(phi, z)
        drift = (i1 - i0) / i0 * 100.0
        print(f"     {label:<18} дрейф за 100 стъпки: {drift:+7.2f} %")
        if bc == "neumann":
            good = abs(drift) < 1.0
    return ok(good, "Нойман: дрейф под 1 %")


def test_door_opens():
    """Долната клетка вече обменя с z[1]."""
    print("\nТЕСТ 3 — вратата се отваря")
    z = make_column()
    rho = np.full_like(z, 1.2)
    # Студена долна клетка, топло отгоре — трябва да се затопли
    T = np.full_like(z, 280.0)
    T[0] = 275.0
    K = np.full_like(z, 0.5)

    dirich = FM.turbulent_diffusion(T.copy(), K, rho, z, 60.0,
                                    bottom="dirichlet")
    neum = FM.turbulent_diffusion(T.copy(), K, rho, z, 60.0,
                                  bottom="neumann", k_door=FM.D1_KH_MAX)
    d_dir = dirich[0] - T[0]
    d_neu = neum[0] - T[0]
    print(f"     Дирихле: ΔT[0] = {d_dir:+.6f} K")
    print(f"     Нойман : ΔT[0] = {d_neu:+.6f} K")
    return (ok(abs(d_dir) < 1e-12, "Дирихле: нулев обмен (както е било)")
            and ok(d_neu > 0, "Нойман: клетката се затопля от z[1]")
            and ok(d_neu < 5.0 - 1e-9, "не прескача целевата температура"))


def test_clip():
    """Клипът ограничава потока; проверява се характерното време."""
    print("\nТЕСТ 4 — клипът на вратата")
    z = make_column()
    rho = np.full_like(z, 1.2)
    T = np.full_like(z, 280.0)
    T[0] = 275.0
    K_big = np.full_like(z, 50.0)      # абсурдно голямо, както нощем

    dz = np.diff(z)
    dz_full = np.concatenate([[dz[0]], 0.5*(dz[:-1]+dz[1:]), [dz[-1]]])
    print(f"     мрежа: dz[0] = {dz[0]:.3f} m  (характерното време е "
          f"dz²/K)")

    free = FM.turbulent_diffusion(T.copy(), K_big, rho, z, 60.0,
                                  bottom="neumann", k_door=None)
    d_free = free[0] - T[0]
    print(f"     без клип          : ΔT[0] = {d_free:+.4f} K")

    monotone, prev = True, None
    for kd in (0.1, 0.01, 0.001, 0.0001):
        out = FM.turbulent_diffusion(T.copy(), K_big, rho, z, 60.0,
                                     bottom="neumann", k_door=kd)
        d = out[0] - T[0]
        tau_min = dz[0] * dz_full[0] / kd / 60.0
        print(f"     клип {kd:>7} m²/s : ΔT[0] = {d:+.4f} K   "
              f"tau = {tau_min:6.2f} min")
        if prev is not None and d >= prev:
            monotone = False
        prev = d

    # При стойността по подразбиране вратата трябва да е БАВНА
    tau_default = dz[0] * dz_full[0] / FM.D1_KH_MAX / 60.0
    dflt = FM.turbulent_diffusion(T.copy(), K_big, rho, z, 60.0,
                                  bottom="neumann", k_door=FM.D1_KH_MAX)
    d_dflt = dflt[0] - T[0]
    print(f"     по подразбиране ({FM.D1_KH_MAX}): ΔT[0] = {d_dflt:+.4f} K"
          f"   tau = {tau_default:.2f} min")

    return (ok(monotone, "по-малък клип → по-малък поток (монотонно)")
            and ok(d_dflt < 0.5 * d_free,
                   "по подразбиране реже потока поне наполовина",
                   f"{d_dflt:+.3f} срещу {d_free:+.3f} K")
            and ok(1.0 <= tau_default <= 30.0,
                   "характерното време е в реалистичния диапазон 1–30 min",
                   f"{tau_default:.2f} min"))


def test_stability():
    """12 часа без NaN и без осцилации."""
    print("\nТЕСТ 5 — стабилност, 720 стъпки")
    z = make_column()
    rho = np.full_like(z, 1.2)
    T = 280.0 - 0.005 * z + 3.0 * np.exp(-z / 50.0)
    K = np.full_like(z, 0.5)

    phi = T.copy()
    prev_d = None
    flips = 0
    for i in range(720):
        new = FM.turbulent_diffusion(phi, K, rho, z, 60.0,
                                     bottom="neumann", k_door=FM.D1_KH_MAX)
        d = new[0] - phi[0]
        if prev_d is not None and prev_d != 0 and np.sign(d) != np.sign(prev_d):
            flips += 1
        prev_d = d
        phi = new
    finite = bool(np.all(np.isfinite(phi)))
    print(f"     смени на знака на dT[0]: {flips}")
    return (ok(finite, "няма NaN/inf")
            and ok(flips <= 2, "няма осцилация през стъпка"))


def test_magnitude():
    """ΔT[0] за час е физически, не 13–28 K като при пряк поток."""
    print("\nТЕСТ 6 — мащаб на промяната за един час")
    z = make_column()
    rho = np.full_like(z, 1.2)
    # Реалистична нощна инверсия: 3 K на първите 50 m
    T = 275.0 + 3.0 * (1.0 - np.exp(-z / 50.0))
    K = np.full_like(z, 0.5)

    phi = T.copy()
    for _ in range(60):
        phi = FM.turbulent_diffusion(phi, K, rho, z, 60.0,
                                     bottom="neumann", k_door=FM.D1_KH_MAX)
    d = phi[0] - T[0]
    print(f"     ΔT[0] за 1 час при клип {FM.D1_KH_MAX} m²/s: {d:+.3f} K")
    print(f"     (за сравнение: C1 без клип даваше 13–28 K)")
    return ok(0.0 < d < 3.0, "промяната е в разумни граници")


def main():
    print("=" * 62)
    print("  D1 — ПРИЕМАТЕЛНИ ТЕСТОВЕ ЗА ОТВАРЯЕМАТА ВРАТА")
    print("=" * 62)
    print(f"  D1_DOOR   = {FM.D1_DOOR}")
    print(f"  D1_KH_MAX = {FM.D1_KH_MAX} m²/s")

    results = []
    for fn in (test_regression, test_conservation, test_door_opens,
               test_clip, test_stability, test_magnitude):
        try:
            results.append(bool(fn()))
        except Exception as e:
            print(f"  ✗ ИЗКЛЮЧЕНИЕ: {e}")
            results.append(False)

    n_ok = sum(results)
    print("\n" + "=" * 62)
    print(f"  {n_ok}/{len(results)} теста минали — "
          f"{'ПРИЕМА СЕ ✓' if n_ok == len(results) else 'НЕ СЕ ПРИЕМА ✗'}")
    print("=" * 62)
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
