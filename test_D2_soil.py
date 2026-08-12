"""
test_D2_soil.py
================
Приемателни тестове за D2 — разхлабен клип на Force-Restore охлаждането
на T_soil в seb_step(). Изпълними ПРЕДИ гейта, нула пускания на модела.

    python test_D2_soil.py

Тества:
  1. РЕГРЕСИЯ — при D2_SOIL=0 резултатът е байт-в-байт стария (0.2 K/hr клип)
  2. T_SKIN/H НЕЗАСЕГНАТИ — D2 пипа само T_soil, не и повърхностния баланс
  3. КЛИПЪТ ДЕЙСТВА — |dT_soil/dt| никога не надхвърля D2_SOIL_MAX_KHR
  4. МОНОТОННОСТ — по-голям клип позволява по-бързо охлаждане, никога по-бавно
  5. ВЪЗПРОИЗВЕЖДА LBGO ДЕФИЦИТА — с измерените стойности от
     LBGO_CFOG_2024-11-01 (ΔT≈4-5K старт), D2 дава охлаждане над 0.2 K/hr,
     докато старото поведение остава залепено на тавана
  6. СТАБИЛНОСТ — 12 стъпки (една нощ) без NaN/inf, T_soil не пресича
     T_skin в грешна посока (пресвръхкорекция)

Изходен код 0 = всички минали.
"""

import os
import sys

import numpy as np

os.environ.setdefault("D2_SOIL", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fog_model as FM


def ok(cond, msg, detail=""):
    print(f"  {'✓' if cond else '✗'} {msg}" + (f"   {detail}" if detail else ""))
    return bool(cond)


# Общи входни условия
COMMON = dict(T_air0=279.0, qv0=0.005, p0=95000.0, rho0=1.2, U0=0.3,
             lwp_col=0.0, sw_down=0.0, dt=3600.0, hour_utc=1.0)


def call(T_skin, T_soil, d2=False, max_khr=1.5):
    os.environ["D2_SOIL"] = "1" if d2 else "0"
    os.environ["D2_SOIL_MAX_KHR"] = str(max_khr)
    # Модулните константи D2_SOIL/D2_SOIL_MAX_KHR се четат при import,
    # но seb_step ги чете НАЖИВО от модула при всяко извикване (глобални
    # имена), затова презаписваме директно атрибутите на модула вместо
    # да разчитаме на re-import.
    FM.D2_SOIL = d2
    FM.D2_SOIL_MAX_KHR = max_khr
    return FM.seb_step(T_skin, T_soil, **COMMON)


def test_regression():
    print("\nТЕСТ 1 — регресия: D2_SOIL=0 = старият клип 0.2 K/hr")
    T_skin, T_soil = 281.0, 283.45   # ΔT = 2.45K, дефицит > 0.2 K/hr
    r_off = call(T_skin, T_soil, d2=False)
    dT_hr = (r_off[3] - T_soil)
    good = ok(abs(dT_hr - (-0.2)) < 1e-9,
             "T_soil промяна = точно -0.2 K/hr при D2 изключено",
             f"получено {dT_hr:+.4f}")
    return good


def test_tskin_unaffected():
    print("\nТЕСТ 2 — T_skin/H незасегнати от D2")
    T_skin, T_soil = 281.0, 283.45
    r_off = call(T_skin, T_soil, d2=False)
    r_on  = call(T_skin, T_soil, d2=True, max_khr=1.5)
    same_tskin = np.isclose(r_off[0], r_on[0], atol=1e-9)
    same_H     = np.isclose(r_off[1], r_on[1], atol=1e-9)
    same_dew   = np.isclose(r_off[2], r_on[2], atol=1e-9)
    diff_soil  = not np.isclose(r_off[3], r_on[3], atol=1e-6)
    return (ok(same_tskin, "T_skin еднакво") and
            ok(same_H, "H еднакво") and
            ok(same_dew, "E_dew еднакво") and
            ok(diff_soil, "T_soil СЕ различава (очаквано)"))


def test_clip_holds():
    print("\nТЕСТ 3 — клипът D2_SOIL_MAX_KHR действа")
    T_skin, T_soil = 270.0, 290.0   # екстремен ΔT=20K, форсира клипа
    for max_khr in (0.5, 1.0, 1.5, 3.0):
        r = call(T_skin, T_soil, d2=True, max_khr=max_khr)
        dT_hr = r[3] - T_soil
        within = abs(dT_hr) <= max_khr + 1e-9
        print(f"     max={max_khr:.1f} K/hr → dT_soil={dT_hr:+.4f} K/hr")
        if not within:
            return ok(False, f"клипът НЕ държи при max={max_khr}")
    return ok(True, "клипът държи при всички тествани тавани")


def test_monotone():
    print("\nТЕСТ 4 — монотонност: по-голям таван → поне толкова охлаждане")
    T_skin, T_soil = 281.0, 285.45   # ΔT=4.45K, реален дефицит вероятно >1К/hr
    prev = None
    monotone = True
    for max_khr in (0.2, 0.5, 1.0, 1.5, 2.0):
        r = call(T_skin, T_soil, d2=(max_khr != 0.2), max_khr=max_khr)
        dT_hr = r[3] - T_soil
        print(f"     max={max_khr:.1f} K/hr → dT_soil={dT_hr:+.4f} K/hr")
        if prev is not None and dT_hr > prev + 1e-9:
            monotone = False
        prev = dT_hr
    return ok(monotone, "охлаждането расте монотонно с тавана")


def test_lbgo_deficit():
    """
    Възпроизвежда измерените стойности от LBGO_CFOG_2024-11-01, 19h:
    Tskin≈5.32°C, Tsoil≈10.10°C (реалният дефицит там иска >0.2 K/hr).
    """
    print("\nТЕСТ 5 — възпроизвежда LBGO дефицита (19h от диагностиката)")
    T_skin = 5.32 + 273.15
    T_soil = 10.10 + 273.15
    r_off = call(T_skin, T_soil, d2=False)
    r_on  = call(T_skin, T_soil, d2=True, max_khr=1.5)
    d_off = r_off[3] - T_soil
    d_on  = r_on[3] - T_soil
    print(f"     стар клип : dT_soil={d_off:+.4f} K/hr  (очаквано -0.200)")
    print(f"     D2 (1.5)  : dT_soil={d_on:+.4f} K/hr")
    return (ok(np.isclose(d_off, -0.2, atol=1e-6),
               "старият клип е точно на тавана (потвърждава диагнозата)")
            and ok(d_on < d_off,
                   "D2 охлажда по-бързо от стария клип",
                   f"{d_on:+.3f} срещу {d_off:+.3f}"))


def test_stability():
    print("\nТЕСТ 6 — стабилност, 12 стъпки (една нощ)")
    T_skin, T_soil = 281.0, 283.45
    hist_soil = [T_soil]
    hist_skin = [T_skin]
    for h in range(12):
        r = call(T_skin, T_soil, d2=True, max_khr=1.5)
        T_skin, T_soil = r[0], r[3]
        hist_soil.append(T_soil)
        hist_skin.append(T_skin)
    finite = all(np.isfinite(x) for x in hist_soil + hist_skin)
    # Почвата не бива да пресече T_skin в грешна посока (overshoot под
    # T_skin, ако е тръгнала над T_skin — би било нефизично засилване)
    started_above = hist_soil[0] > hist_skin[0]
    no_overshoot_below = all(
        (s >= sk - 0.5) for s, sk in zip(hist_soil, hist_skin)
    ) if started_above else True
    print(f"     T_soil: {hist_soil[0]-273.15:.2f} → {hist_soil[-1]-273.15:.2f} °C")
    print(f"     T_skin: {hist_skin[0]-273.15:.2f} → {hist_skin[-1]-273.15:.2f} °C")
    return (ok(finite, "няма NaN/inf")
            and ok(no_overshoot_below, "няма нефизичен overshoot"))


def main():
    print("=" * 62)
    print("  D2 — ПРИЕМАТЕЛНИ ТЕСТОВЕ ЗА РАЗХЛАБЕНИЯ ПОЧВЕН КЛИП")
    print("=" * 62)

    results = []
    for fn in (test_regression, test_tskin_unaffected, test_clip_holds,
               test_monotone, test_lbgo_deficit, test_stability):
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
