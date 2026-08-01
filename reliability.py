# -*- coding: utf-8 -*-
"""
reliability.py — надеждност на конкретната прогноза
====================================================
Не мени прогнозата. Добавя ѝ ред, който казва колко да ѝ се вярва.

    from reliability import assess, rh_early_max_from_history
    r = assess("LBGO", forecast_fog=True, rh_early=0.982)
    print(r["line"])

Числата идват от reliability.json, произведен от calibrate_reliability.py
върху набора от случаи. Не са зашити тук — при разширяване на набора се
преизчисляват.

Защо съществува
---------------
Измерено на 288 случая: прогнозата за мъгла на континентална станция е
вярна в 49 % от случаите (интервал 38–61). Тоест е монета. Прогнозата
"ясно" е вярна в 84 %.

По-полезното: когато моделът каже ЯСНО, а приземната влажност в
18–22 UTC е над 95 %, прогнозата е вярна само в 40 % — тоест шест от
десет такива нощи са пропуснати мъгли. Фишер p = 0.0013.

Обратното разслояване (прогноза за мъгла) НЕ е значимо (p = 0.077) и
не се ползва — съзнателно, за да не се съобщава несъществуваща
информация.

Търсенето на разделител между верните и грешните прогнози за мъгла
беше изчерпано отделно: нито един признак и нито една двойка от
наличните величини не ги дели над 10 % превес при отложена извадка.
Затова тук не се обещава такова разделяне.
"""
import os, json

_HERE = os.path.dirname(os.path.abspath(__file__))
_CAL_PATH = os.path.join(_HERE, "reliability.json")
_CAL = None
COASTAL = {"LBWN", "LBBG"}


def load(path=None):
    """Зарежда калибрацията. Връща None, ако файлът липсва."""
    global _CAL
    p = path or _CAL_PATH
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            _CAL = json.load(f)
    except (OSError, json.JSONDecodeError):
        _CAL = None
    return _CAL


def rh_early_max_from_history(history, h0=18.0, h1=22.0):
    """
    Максимална приземна относителна влажност в ранната нощ.

    Величината е ДИАГНОСТИКА НА МОДЕЛА, не наблюдение — тоест е
    налична и в оперативен режим, веднага след 22 UTC стъпката.
    """
    vals = []
    for r in history:
        h = r.get("hour_utc")
        if h is None:
            continue
        hh = h + 24.0 if h < h0 - 12.0 else h
        if h0 <= hh <= h1:
            v = r.get("rh_sfc")
            if v is not None:
                vals.append(float(v))
    return max(vals) if vals else None


def _group_for(icao):
    """
    Най-специфичната група с достатъчно случаи, плюс обединената.

    Йерархия: базовата честота идва от станцията, ако извадката ѝ
    стига; условният ефект (разслояването) — от обединената група, ако
    собственият ѝ не е значим. Условният ефект е физически режим и
    вероятно е общ за континенталните станции, докато базовата честота
    е специфична за терена.
    """
    cal = _CAL or load()
    if not cal:
        return None, None, None
    g = cal.get("groups", {})
    pooled_name = "coastal" if icao in COASTAL else "continental"
    pooled = g.get(pooled_name)
    if icao in g:
        b = g[icao]
        if b.get("fog", {}).get("usable") and b.get("clear", {}).get("usable"):
            return icao, b, pooled
    return (pooled_name, pooled, pooled) if pooled else (None, None, None)


def assess(icao, forecast_fog, rh_early=None):
    """
    Оценка на надеждността.

    icao          : ICAO код на летището
    forecast_fog  : True, ако моделът прогнозира мъгла
    rh_early      : максимална приземна RH 18–22 UTC (0–1), или None

    Връща dict с ключове:
      level   : 'надеждна' | 'умерена' | 'ниска' | 'неоценена'
      rate    : дял верни прогнози от този вид, или None
      n       : брой случаи, върху които е измерено
      line    : готов ред за печат
      detail  : пояснение, или None
    """
    name, blk, pooled = _group_for(icao)
    if not blk:
        return {"level": "неоценена", "rate": None, "n": 0,
                "line": "[НАДЕЖДНОСТ] няма калибрация — "
                        "пусни calibrate_reliability.py",
                "detail": None}

    side = "fog" if forecast_fog else "clear"
    d = blk.get(side, {})
    n, rate = d.get("n", 0), d.get("rate")
    if not n or rate is None:
        return {"level": "неоценена", "rate": None, "n": 0,
                "line": f"[НАДЕЖДНОСТ] {name}: няма случаи от този вид",
                "detail": None}

    eff_rate, eff_n, detail = rate, n, None
    sp = d.get("split") or {}
    # Ако собственото разслояване не е значимо, ползваме обединеното —
    # условният ефект е режим, базовата честота е терен.
    src = "станция"
    if not sp.get("significant") and pooled is not None:
        psp = (pooled.get(side) or {}).get("split") or {}
        if psp.get("significant"):
            sp, src = psp, "обединено"
    thr = sp.get("thr")
    if (sp.get("significant") and rh_early is not None and thr is not None):
        part = sp["above"] if rh_early >= thr else sp["below"]
        if part.get("rate") is not None and part.get("n", 0) >= 5:
            eff_rate, eff_n = part["rate"], part["n"]
            rel = "над" if rh_early >= thr else "под"
            detail = (f"ранна влажност {rh_early:.0%} е {rel} {thr:.0%}; "
                      f"в този режим {part['correct']}/{part['n']}")
            if src == "обединено":
                detail += " (обединено за групата)"

    if not d.get("usable"):
        level = "неоценена"
    elif eff_rate >= 0.80:
        level = "надеждна"
    elif eff_rate >= 0.60:
        level = "умерена"
    else:
        level = "ниска"

    what = "МЪГЛА" if forecast_fog else "ЯСНО"
    line = (f"[НАДЕЖДНОСТ] прогноза {what} · {level} · "
            f"{eff_rate:.0%} верни ({eff_n} случая, {name})")
    if detail:
        line += f" · {detail}"
    if not d.get("usable"):
        line += " · ИЗВАДКАТА Е МАЛКА"
    # Проверката е за САМАТА станция, не за групата, върху която се
    # смятат числата. LBBG няма нито един мъглив случай, но се пада на
    # "coastal", където Варна има девет — статистиката е валидна, ала
    # моделът не е оценяван по мъгла на самото летище.
    own = (_CAL or {}).get("groups", {}).get(icao, {})
    if own.get("n_fog_cases", 1) == 0:
        line += (f" · ВНИМАНИЕ: {icao} няма нито един мъглив случай в "
                 f"набора — моделът не е оценяван по основната си задача "
                 f"на това летище")
    return {"level": level, "rate": eff_rate, "n": eff_n,
            "line": line, "detail": detail, "group": name}


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout, sys.stderr):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError): pass
    if not load():
        sys.exit("Няма reliability.json — пусни calibrate_reliability.py")
    print("САМОПРОВЕРКА\n")
    cases = [
        ("LBGO", True, 0.99, "мъгла, влажна ранна нощ"),
        ("LBGO", False, 0.99, "ясно, но влажна ранна нощ ← важният случай"),
        ("LBGO", False, 0.70, "ясно, суха ранна нощ"),
        ("LBSF", True, 0.98, "мъгла, София"),
        ("LBSF", False, 0.97, "ясно при висока влажност, София"),
        ("LBWN", True, 0.98, "мъгла, Варна"),
        ("LBBG", False, 0.60, "ясно, Бургас — нула мъглени случая"),
        ("LBGO", True, None, "без стойност за влажността"),
    ]
    for icao, fog, rh, note in cases:
        r = assess(icao, fog, rh)
        print(f"  {note}")
        print(f"    {r['line']}\n")
