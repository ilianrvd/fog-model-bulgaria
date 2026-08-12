"""
verify_cases.py
===============
Верификация на fog модела върху локална библиотека от ситуации (cases/),
подбрани от case_selector.py. Без OGIMET при всеки пуск: METAR идват от
файловете, ICON се кешира локално при първото теглене.

Схема на оценка (по плана от 12.07.2026):
  1) СЪБИТИЙНА метрика (оперативен критерий): "епизод VIS < 2000m тази нощ"
     — HIT/MISS/FA/CN + Δt на началото (timing се следи, не наказва)
  2) ЧАСОВА метрика (диагностика): POD/FAR/CSI на праг 1000m
  3) T метрики (етап 1): MAE(T), грешка T_min, грешка T в 03–06 UTC

Етапи по летище: CDRY (T верига) → CFOG (мъгла) → CLDY (облачност) → DYNM (режими)

Регресионен гейт: --accept ИМЕ замразява текущите резултати като база
(baselines/ИМЕ.json). Всеки следващ пуск сравнява и крещи РЕГРЕСИЯ,
ако приет случай се влоши. Така "запазваме София", докато работим по Варна.

Употреба:
    python verify_cases.py                          # всички ситуации
    python verify_cases.py --airport LBSF           # само София
    python verify_cases.py --category CDRY          # само една категория
    python verify_cases.py --airport LBSF --accept LBSF-stage1
    python verify_cases.py --list

Формат на файловете в cases/:  {ICAO}_{CAT}_{YYYY-MM-DD}.txt
  Редове, започващи с '#', са метаданни; останалите се проверяват срещу
  METAR шаблон. Категории: CFOG, CDRY, CLDY, DYNM.
"""

import sys, os, re, json, glob, time, argparse, hashlib
import numpy as np
from datetime import datetime, timedelta, timezone
import pairing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ──────────────────────────────────────────────────────────────────────────
# Конфигурация
# ──────────────────────────────────────────────────────────────────────────
CASES_DIR     = "cases"
ICON_CACHE    = "icon_cache"
BASELINE_DIR  = "baselines"
LOGS_DIR      = "logs"

START_HOUR    = 18      # UTC старт на модела (--hour)
FORECAST_H    = 15      # хоризонт, покрива нощта до 09 UTC

EVENT_VIS     = 2000.0  # праг на събитийната метрика [m]
HOURLY_VIS    = 1000.0  # праг на часовата (диагностична) метрика [m]

# Праговете за епизоди, карантината и сдвояването живеят в pairing.py —
# единствен източник за трите места, които сравняват модел с METAR.
# Старите EVENT_MIN_HRS / EVENT_MIN_HRS_OBS брояха ИНДЕКСИ в списък и
# затова мълчаливо зависеха от каденцата на изхода; новите са в часове
# и в брой проби. Старата карантина EVENT_END_UTC=7 реално отсичаше в
# 08:00 (изключваше цели часове 8..15) — поведението е запазено едно към
# едно като pairing.WIN_END_UTC = 8.0.

# Критерии "готово" за етап 1 (CDRY, T верига)
STAGE1_TMIN_ERR   = 1.5   # |грешка на T_min| < 1.5°C
STAGE1_ERR_0306   = 2.0   # ср. |ΔT| в 03–06 UTC < 2.0°C

CATEGORIES = ("CFOG", "CDRY", "CLDY", "DYNM")

# ──────────────────────────────────────────────────────────────────────────
# Мини METAR парсер (само каквото трябва за верификация: време, T, Td, VIS)
# Независим от metar_parser, за да не зависим от вътрешните му ключове.
# ──────────────────────────────────────────────────────────────────────────
_METAR_RE = re.compile(r"^(?:METAR\s+|SPECI\s+)?(LB[A-Z]{2})\s+(\d{2})(\d{2})(\d{2})Z")
_TT_RE    = re.compile(r"\s(M?\d{2})/(M?\d{2})\s")
_VIS_RE   = re.compile(r"\s(\d{4})(?:NDV)?\s")

def _t2f(s):
    return -float(s[1:]) if s.startswith("M") else float(s)

def parse_metar_light(raw, base_date):
    """Извлича (datetime UTC, T, Td, vis_m, fog_bool) от суров METAR.
    base_date = датата на ден D на нощта; денят в METAR определя D или D+1."""
    m = _METAR_RE.match(raw.strip())
    if not m:
        return None
    day, hh, mm = int(m.group(2)), int(m.group(3)), int(m.group(4))
    d0 = datetime.strptime(base_date, "%Y-%m-%d")
    for cand in (d0, d0 + timedelta(days=1), d0 - timedelta(days=1)):
        if cand.day == day:
            dt = cand.replace(hour=hh, minute=mm, tzinfo=timezone.utc)
            break
    else:
        return None
    T = Td = None
    mt = _TT_RE.search(raw)
    if mt:
        T, Td = _t2f(mt.group(1)), _t2f(mt.group(2))
    vis = None
    if "CAVOK" in raw or " 9999" in raw:
        vis = 10000.0
    else:
        mv = _VIS_RE.search(raw)
        if mv:
            vis = float(mv.group(1))
    fog = bool(re.search(r"\bFG\b", raw))
    return {"dt": dt, "T": T, "Td": Td, "vis": vis, "fog": fog, "raw": raw.strip()}


# ──────────────────────────────────────────────────────────────────────────
# Причина за ниската видимост в НАБЛЮДЕНИЕТО (11.08.2026)
#
# Мотив: LBSF_CLDY_2024-01-19 се броеше за HIT, а реалната ниска видимост
# е от снеговалеж при 16 kt (TEMPO SN BLSN), не от мъгла. Обратно — пет
# случая се брояха за MISS по същата причина. Правилото "снежните случаи
# са извън обхвата" съществуваше, но не се прилагаше никъде в кода.
#
# Класификацията е ЧИСТО ВЕРИФИКАЦИОННА — не влиза в run_case.py и не
# променя нито ред физика. Изключените случаи не участват в CSI, но се
# отчитат отделно като EXCL, за да остане n честно.
# ──────────────────────────────────────────────────────────────────────────

# Редът има значение: по-специфичните токени първи (FZFG преди FG,
# BLSN преди SN), иначе краткият се хваща вътре в дългия.
_WX_FOG   = ("FZFG", "MIFG", "BCFG", "PRFG", "FG")
_WX_MIST  = ("BR",)
_WX_SNOW  = ("BLSN", "DRSN", "SHSN", "SNRA", "SG", "PL", "GS", "SN")
_WX_RAIN  = ("FZDZ", "FZRA", "SHRA", "DZ", "RA")

# Прогнозната част не е наблюдение — TEMPO 0800 FZFG не значи, че
# в момента има мъгла.
_WX_TREND_RE = re.compile(r"\b(?:TEMPO|BECMG|NOSIG|PROB\d{2})\b")


def classify_obs_wx(raw):
    """Причина за видимостта в един METAR: FOG/MIST/SNOW/RAIN/MIXED/OTHER."""
    body = _WX_TREND_RE.split(raw)[0]
    fog  = any(re.search(r"\b" + t + r"\b", body) for t in _WX_FOG)
    mist = any(re.search(r"\b" + t + r"\b", body) for t in _WX_MIST)
    snow = any(re.search(r"[-+]?\b" + t + r"\b", body) for t in _WX_SNOW)
    rain = any(re.search(r"[-+]?\b" + t + r"\b", body) for t in _WX_RAIN)
    if (snow or rain) and (fog or mist): return "MIXED"
    if snow: return "SNOW"
    if rain: return "RAIN"
    if fog:  return "FOG"
    if mist: return "MIST"
    return "OTHER"


def diagnose_obs_cause(obs_list, threshold=None):
    """
    Гледа само часовете с наблюдавана видимост под прага и решава коя е
    доминиращата причина. Праг по подразбиране = EVENT_VIS, тоест същият,
    по който се определя събитието — иначе бихме изключвали случай заради
    часове, които не влизат в метриката.

    Връща (excluded: bool, reason: str|None, counts: dict).
    """
    thr = EVENT_VIS if threshold is None else threshold
    counts = {}
    n = 0
    for o in obs_list:
        if o.get("vis") is None or o["vis"] >= thr:
            continue
        c = classify_obs_wx(o["raw"])
        counts[c] = counts.get(c, 0) + 1
        n += 1

    if n == 0:
        return False, None, counts

    # Доминиране = поне половината часове с ниска видимост. Прагът е нисък
    # съзнателно: смесените нощи (сняг + мъгла) остават ВЪТРЕ като MIXED,
    # защото при тях моделът все пак има какво да улови.
    if counts.get("SNOW", 0) >= 0.5 * n:
        return True, "SNOW_DOMINATED", counts
    if counts.get("SNOW", 0) + counts.get("RAIN", 0) >= 0.5 * n:
        return True, "PRECIP_DOMINATED", counts
    return False, None, counts


def _selftest_obs_cause():
    """Приемателни тестове за класификатора. Изпълними ПРЕДИ гейта:
        python verify_cases.py --selftest
    Върнатата стойност е кодът за изход (0 = минали)."""
    tests = [
        ("METAR LBSF 200700Z 28015KT 1100 R27/1600U -SN BKN008 OVC020 "
         "M02/M03 Q1019 TEMPO 1500 SN", "SNOW"),
        ("METAR LBSF 180700Z 30004KT 270V330 0900 R27/1700U FZFG OVC004 "
         "M04/M05 Q1033 TEMPO 0800 FZFG", "FOG"),
        ("METAR LBSF 171630Z AUTO VRB02KT 2400 BR OVC002/// M03/M03 "
         "Q1036 TEMPO 0800 FZFG", "MIST"),
        ("METAR LBSF 200230Z AUTO 27016KT 9000 -FZDZ FEW010/// M01/M02 "
         "Q1016 REFZRA TEMPO 2000 SN", "RAIN"),
        ("METAR LBSF 240800Z 27004KT CAVOK 01/M03 Q1029 NOSIG", "OTHER"),
        ("METAR LBSF 200300Z AUTO 27019KT 6000 -SN FEW013/// BR M01/M02 "
         "Q1016 TEMPO 2000 SN", "MIXED"),
        # TEMPO не е наблюдение: FZFG е само в прогнозната част
        ("METAR LBSF 171600Z AUTO VRB01KT 2400 BR OVC002/// M03/M03 "
         "Q1036 TEMPO 0800 FZFG", "MIST"),
    ]
    bad = 0
    print("ТЕСТ 1 — класификация на METAR:")
    for raw, want in tests:
        got = classify_obs_wx(raw)
        if got != want:
            bad += 1
            print(f"  ✗ очаквано {want}, получено {got}: {raw[:60]}")
    print(f"  {len(tests) - bad}/{len(tests)} минали")

    # Тест 2 — цели случаи, ако файловете са налични
    known = [("LBSF_CLDY_2024-01-19", True,  "SNOW_DOMINATED"),
             ("LBSF_CLDY_2024-01-23", False, None),
             ("LBSF_CLDY_2025-01-17", False, None),
             ("LBPD_CLDY_2025-02-01", False, None)]
    print("ТЕСТ 2 — цели случаи:")
    n_run = 0
    for cid, want_excl, want_reason in known:
        path = os.path.join(CASES_DIR, cid + ".txt")
        if not os.path.exists(path):
            continue
        n_run += 1
        try:
            _, _, _, obs = load_case_file(path)
        except Exception as e:
            bad += 1
            print(f"  ✗ {cid}: {e}")
            continue
        excl, reason, _ = diagnose_obs_cause(obs)
        if excl != want_excl or reason != want_reason:
            bad += 1
            print(f"  ✗ {cid}: очаквано ({want_excl}, {want_reason}), "
                  f"получено ({excl}, {reason})")
    print(f"  {n_run} проверени, {'без разминавания' if not bad else 'ИМА РАЗМИНАВАНИЯ'}")

    # Тест 3 — гейтът различава ИЗКЛЮЧЕН от ЛИПСВАЩ.
    # Първата версия на кръпката ги смесваше: изключените изпадаха от
    # cur и гейтът вдигаше фалшива тревога "провери cases/".
    print("ТЕСТ 3 — гейт: изключен ≠ липсващ:")
    _res = [
        {"case_id": "X_CLDY_2024-01-19", "icao": "X", "category": "CLDY",
         "excluded": True, "excluded_reason": "SNOW_DOMINATED",
         "eval": {"event": "HIT", "T": {"MAE": 1.2},
                  "hourly": {"CSI": 0.5}}},
        {"case_id": "X_CFOG_2024-01-10", "icao": "X", "category": "CFOG",
         "excluded": False, "excluded_reason": None,
         "eval": {"event": "HIT", "T": {"MAE": 1.0},
                  "hourly": {"CSI": 0.6}}},
    ]
    _base = {"X-v1": {"cases": {
        "X_CLDY_2024-01-19": {"event": "HIT", "T_MAE": 1.2},
        "X_CFOG_2024-01-10": {"event": "HIT", "T_MAE": 1.0},
        "X_CDRY_2099-01-01": {"event": "CN", "T_MAE": 1.0}}}}
    _r, _m, _e, _x = check_regressions(_res, _base)
    if _m != ["X-v1: X_CDRY_2099-01-01"]:
        bad += 1
        print(f"  ✗ липсващи: очаквано 1 непознат случай, получено {_m}")
    if _x != ["X-v1: X_CLDY_2024-01-19"]:
        bad += 1
        print(f"  ✗ изключени: очаквано 1, получено {_x}")
    if _r:
        bad += 1
        print(f"  ✗ фалшиви регресии: {_r}")
    print("  разделянето работи" if not _r and
          _m == ["X-v1: X_CDRY_2099-01-01"] else "  ПРОБЛЕМ")

    print("\nСАМОТЕСТ:", "ПРЕМИНАТ ✓" if bad == 0 else f"ПАДНАЛ ✗ ({bad})")
    return 0 if bad == 0 else 1


def load_case_file(path):
    """Чете файл от cases/ → (icao, category, date_str, [obs...])."""
    name = os.path.splitext(os.path.basename(path))[0]
    parts = name.split("_")
    if len(parts) != 3 or parts[1] not in CATEGORIES:
        raise ValueError(f"Неочаквано име: {name} (искам ICAO_CAT_YYYY-MM-DD)")
    icao, cat, date_str = parts[0], parts[1], parts[2]
    obs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            o = parse_metar_light(line, date_str)
            if o:
                obs.append(o)
    obs.sort(key=lambda o: o["dt"])
    return icao, cat, date_str, obs


# ──────────────────────────────────────────────────────────────────────────
# ICON кеш
# ──────────────────────────────────────────────────────────────────────────
def _np_restore(obj, key=None):
    """Рекурсивно: числови list-ове от JSON кеша → numpy масиви.
    cc_series остава list от тройки (моделът я индексира и разопакова)."""
    if isinstance(obj, dict):
        return {k: _np_restore(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        if key == "cc_series":
            return obj
        if obj and all(isinstance(x, (int, float)) for x in obj):
            return np.asarray(obj, dtype=float)
        return [_np_restore(x, key) for x in obj]
    return obj


def fetch_icon_cached(icao, date_str, hour, forecast_hours):
    os.makedirs(ICON_CACHE, exist_ok=True)
    key = f"{icao}_{date_str}_{hour:02d}_{forecast_hours}"
    path = os.path.join(ICON_CACHE, key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            prof = json.load(f)
        # Кешът от стария run_case няма cc_series — инвалидирай го.
        # Тройки (без ICON rh2m) също са стар формат → презапис.
        _cc = prof.get("cc_series")
        if _cc is not None and (len(_cc) == 0 or len(_cc[0]) >= 5):
            return _np_restore(prof)
        print(f"  [кеш] {key}: стар формат cc_series (< 5 елемента) → презаписвам")
        os.remove(path)
    from run_case import fetch_icon_historical
    prof = fetch_icon_historical(icao, date_str, hour0=hour,
                                 forecast_hours=forecast_hours)
    def _conv(o):
        if isinstance(o, np.integer):  return int(o)
        if isinstance(o, np.floating): return float(o)
        if isinstance(o, np.ndarray):  return o.tolist()
        raise TypeError(type(o))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, default=_conv)
    return prof


# ──────────────────────────────────────────────────────────────────────────
# Един рун на модела (интеграционната логика от batch_test.py, вход локален)
# ──────────────────────────────────────────────────────────────────────────
def run_model(icao, date_str, hour, obs_list):
    from run_case import (build_surface_layer, diagnose_regime,
                          apply_nudging, AIRPORT_CONFIG)
    try:
        from run_case import apply_metar_correction
    except ImportError:
        from metar_parser import apply_metar_correction
    from metar_parser import parse_metar
    from fog_model import FogModel1D, _sin_elevation

    cfg = AIRPORT_CONFIG[icao]
    doy = datetime.strptime(date_str, "%Y-%m-%d").timetuple().tm_yday

    # Стартов METAR: най-близкият до стартовия час (±45 мин)
    t_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, tzinfo=timezone.utc)
    near = [o for o in obs_list if abs((o["dt"] - t_start).total_seconds()) <= 2700]
    start_obs = min(near, key=lambda o: abs((o["dt"] - t_start).total_seconds())) \
                if near else None
    metar_raw  = start_obs["raw"] if start_obs else ""
    metar_dict = parse_metar(metar_raw) if metar_raw else {}

    profile = fetch_icon_cached(icao, date_str, hour, FORECAST_H + 1)
    if metar_dict:
        profile = apply_metar_correction(profile, metar_dict)
    profile = build_surface_layer(profile, metar_dict, doy)

    regime, tau, reason = diagnose_regime(profile, metar_dict, cfg)

    z_log   = np.logspace(np.log10(0.5), np.log10(50), 20)
    z_lin   = np.linspace(55, 2000, 20)
    z_model = np.concatenate([z_log, z_lin])
    T_m  = np.interp(z_model, profile["z"], profile["T"])
    qv_m = np.interp(z_model, profile["z"], profile["qv"])
    p_m  = np.interp(z_model, profile["z"], profile["p"])
    u_m  = np.interp(z_model, profile["z"], profile["u"])
    v_m  = np.interp(z_model, profile["z"], profile["v"])

    # Слънчевата геометрия по дължина — същият източник като run_case
    import fog_model as _fm
    from run_case import AIRPORT_COORDS as _AC
    _c = _AC[icao]
    _fm.set_solar_site(_c["lat"], _c["lon"])

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=float(hour), dt=60, day_of_year=doy)
    model.cc_series = profile.get("cc_series", [])

    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        # fix_soil (LBWN): ICON морска клетка дава T_soil=0-3°C при
        # реални 8-14°C — коригираме до T_air_2m + 2°C (model.T[0] е
        # приземното ниво от METAR след build_surface_layer)
        if cfg.get("fix_soil") and (model.T_soil - 273.15) < 5.0:
            model.T_soil = max(model.T_soil, float(model.T[0]) + 2.0)
        model.T_skin = min(float(model.T_soil), model.T[0])

    ql_init_raw = profile.get("ql_init")
    if ql_init_raw is not None and len(ql_init_raw) == len(profile["z"]):
        model.ql = np.interp(z_model, profile["z"], ql_init_raw)
    elif ql_init_raw is not None and np.any(np.array(ql_init_raw) > 0):
        model.ql = np.where(z_model < 50, float(np.max(ql_init_raw)), 0.0)
    else:
        model.ql = np.zeros(len(z_model))

    hourly_profs = profile.get("hourly_profiles", [])
    steps_total, steps_per_hr, dt = FORECAST_H * 60, 60, 60
    # Каденца на записа в history. Нудж-логиката по-долу остава ЧАСОВА
    # (steps_per_hr) — сменяме само колко често се снима състоянието.
    steps_out = int(pairing.OUTPUT_INTERVAL_MIN * 60 / dt)

    import io as _io
    current_regime, current_tau = regime, tau
    pending_regime, pending_count = None, 0
    regime_log = [{"hour_utc": hour, "regime": regime, "reason": reason}]

    model.diagnose()
    for step in range(1, steps_total + 1):
        model.step()
        hour_elapsed = step * dt / 3600.0
        prof_idx = min(int(hour_elapsed), max(len(hourly_profs) - 1, 0))

        if step % steps_per_hr == 0 and hourly_profs:
            hour_now  = (float(hour) + hour_elapsed) % 24
            hour_next = (hour_now + 1) % 24
            sin_el      = _sin_elevation(hour_now,  doy)
            sin_el_next = _sin_elevation(hour_next, doy)
            is_sunrise  = sin_el > 0.05 and sin_el_next > sin_el

            remaining = hourly_profs[prof_idx:]
            if len(remaining) < 3:
                remaining = hourly_profs[-3:]

            # D4 FIX (само за крайбрежни): текущ ICON вятър вместо {} —
            # при Варна/Бургас ICON вятърът е надежден адвективен сигнал;
            # при София котловината ICON надценява вятъра под инверсия
            # (01-19) → континенталните запазват старото поведение ({}).
            #
            # ЗАБЕЛЕЖКА (18.07.2026): унифицираният осреднен вятър тестван
            # и отхвърлен — вижте run_case.py за детайли. Върнато coastal-only.
            #
            # v1.4-SYNC (26.07.2026): блокът по-долу е ДОСЛОВНО копие на
            # run_case.py, редове 848-880. До тази дата verify_cases.py
            # съдържаше само v1.3 логиката (без поривен критерий), заради
            # което гейтът от 26.07 отчете "бит-идентични" резултати —
            # кръпката v1.4 просто не се е изпълнявала тук. При всяка
            # бъдеща промяна в run_case.py тези два блока се променят
            # ЗАЕДНО, иначе гейтът мери друг код.
            # v20 (27.07.2026): по флага gust_regime, не по coastal.
            # ДОСЛОВНО като run_case.py.
            if cfg.get("gust_regime"):
                # ── Двоен критерий вятър+порив (v1.4) ──
                #   RADIATIVE/ADVECTIVE → DYNAMIC :  V >= 4kt И Gust >= 8kt
                #   DYNAMIC → RADIATIVE           :  V <  4kt И Gust <  8kt
                #   смесено                       :  запазва текущия режим
                # 20.0 е SENTINEL (=10.3 m/s > прага 4 m/s в diagnose_regime),
                # а не измерена стойност — начин да се форсира DYNAMIC.
                # Fallback към v1.3 поведение, ако поривът липсва.
                _cur_wind = hourly_profs[min(prof_idx, len(hourly_profs) - 1)]
                _cur_u = float(_cur_wind["u"][0]) if "u" in _cur_wind else 0.0
                _cur_v = float(_cur_wind["v"][0]) if "v" in _cur_wind else 0.0
                _cur_wspd_kt = float(np.hypot(_cur_u, _cur_v)) / 0.5144
                _gust_kt = _cur_wind.get("gust10")

                # v21: прагове по летище — ДОСЛОВНО като run_case.py
                _v_thr, _g_thr = cfg.get("gust_thresholds", (4.0, 8.0))

                metar_reassess = dict(metar_dict)
                if _gust_kt is None:
                    metar_reassess["wind_speed"] = _cur_wspd_kt
                elif current_regime == "dynamic":
                    # Излизане само ако И двете са под праг
                    if _cur_wspd_kt < _v_thr and _gust_kt < _g_thr:
                        metar_reassess["wind_speed"] = 0.0
                    else:
                        metar_reassess["wind_speed"] = 20.0
                else:
                    # Влизане само ако И двете са над праг
                    if _cur_wspd_kt >= _v_thr and _gust_kt >= _g_thr:
                        metar_reassess["wind_speed"] = 20.0
                    else:
                        metar_reassess["wind_speed"] = _cur_wspd_kt
            else:
                # Континентални: замразеният стартов METAR (както run_case),
                # НЕ празен {} — иначе V_sfc=0 и при стартов вятър >4 m/s
                # (03-14: 27008KT=4.1 m/s) reassessment губи DYNAMIC режима
                # и охлажда свободно под облаци → -22°C артефакт.
                metar_reassess = metar_dict

            _old = sys.stdout; sys.stdout = _io.StringIO()
            cand_regime, cand_tau, cand_reason = diagnose_regime(
                {"hourly_profiles": remaining}, metar_reassess, cfg)
            sys.stdout = _old

            if is_sunrise and current_regime == "radiative":
                cand_regime, cand_tau = "dynamic", 7200
                cand_reason = "Изгрев → nudging T"
            if current_regime == "dynamic" and cand_regime == "radiative" \
               and is_sunrise:
                cand_regime, cand_tau = "dynamic", current_tau

            if cand_regime != current_regime:
                pending_count = pending_count + 1 \
                    if cand_regime == pending_regime else 1
                pending_regime = cand_regime
                threshold = 1 if is_sunrise else 2
                if pending_count >= threshold:
                    regime_log.append({"hour_utc": (hour + hour_elapsed) % 24,
                                       "regime": cand_regime,
                                       "reason": cand_reason})
                    current_regime, current_tau = cand_regime, cand_tau
                    pending_regime, pending_count = None, 0
            else:
                pending_regime, pending_count = None, 0

        if current_tau and hourly_profs:
            apply_nudging(model, hourly_profs[prof_idx],
                          cfg["tau_T"], current_tau)

        if step % steps_out == 0:
            model.diagnose()

    return model.history, regime_log


# ──────────────────────────────────────────────────────────────────────────
# Оценка
# ──────────────────────────────────────────────────────────────────────────
def _model_times(history, hour, date_str):
    """Реални UTC моменти на моделните записи."""
    t0 = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, tzinfo=timezone.utc)
    out = []
    for h in history:
        he = h.get("time_h")
        if he is None:
            he = (h["hour_utc"] - hour) % 24
        out.append(t0 + timedelta(hours=float(he)))
    return out


def _pairs(history, obs_list, hour, date_str):
    """
    Сдвоява всяко НАБЛЮДЕНИЕ с най-близкия свободен моделен запис.

    Старата версия обхождаше моделните часове и всеки си вземаше най-близък
    METAR; при станции на 30 мин точният час винаги печелеше и половината
    наблюдения се губеха (LBGO: 14 от 30 в нощта). Освен това един и същ
    METAR можеше да обслужи два моделни часа, когато кръглият липсва.

    Връща [(t_model, history_rec, obs)] по време на наблюдението.
    """
    mt = _model_times(history, hour, date_str)
    ot = [o["dt"] for o in obs_list]
    return [(mt[j], history[j], obs_list[i])
            for i, j, _ in pairing.pair_obs_to_model(ot, mt)]


def evaluate(history, obs_list, hour, date_str):
    mt    = _model_times(history, hour, date_str)
    prs   = _pairs(history, obs_list, hour, date_str)

    # ── часова метрика (праг 1000m) + T метрики
    hits = miss = fa = cn = 0
    mae_v, mae_t, err_0306 = [], [], []
    tmods, tobs_l = [], []
    obs_t_series, obs_ev = [], []
    for t, h, o in prs:
        vm = float(h["vis_sfc"])
        tm = float(h["T_sfc"]) - 273.15
        if o["vis"] is None:
            continue
        vo = o["vis"]
        obs_t_series.append(o["dt"])
        obs_ev.append(vo < EVENT_VIS)
        mae_v.append(abs(vm - vo))
        pm, po = vm < HOURLY_VIS, vo < HOURLY_VIS
        if pm and po:       hits += 1
        elif pm and not po: fa   += 1
        elif po and not pm: miss += 1
        else:               cn   += 1
        if o["T"] is not None:
            # Tmin от двете страни — САМО върху сдвоените моменти, за да
            # не зависи от каденцата на моделния изход (при по-гъст изход
            # моделният минимум може само да падне).
            tmods.append(tm)
            tobs_l.append(o["T"])
            mae_t.append(abs(tm - o["T"]))
            if 3 <= t.hour <= 6:
                err_0306.append(abs(tm - o["T"]))

    pod = hits / (hits + miss) if (hits + miss) else None
    far = fa / (hits + fa)     if (hits + fa)   else None
    csi = hits / (hits + miss + fa) if (hits + miss + fa) else None

    # T_min грешка
    t_min_err = None
    if tobs_l and tmods:
        t_min_err = min(tmods) - min(tobs_l)

    # ── събитийна метрика (изгревна карантина: pairing.WIN_END_UTC)
    # Двете серии се строят всяка на СВОЯТА времева база — моделната от
    # моделния изход, наблюдаваната само от реални METAR-и. Старият код ги
    # държеше в един индексиран списък и вписваше False за липсващ METAR,
    # което при по-гъст моделен изход би накъсало наблюдаваните епизоди.
    mod_ev_w = [float(h["vis_sfc"]) < EVENT_VIS and pairing.in_night_window(t)
                for t, h in zip(mt, history)]
    obs_ev_w = [e and pairing.in_night_window(t)
                for t, e in zip(obs_t_series, obs_ev)]

    m_eps = pairing.episodes(mt, mod_ev_w,
                             min_dur_h=pairing.EVENT_MIN_DUR_H,
                             max_gap_h=pairing.MAX_GAP_H)
    o_eps = pairing.episodes(obs_t_series, obs_ev_w,
                             min_count=pairing.EVENT_MIN_OBS_N,
                             max_gap_h=pairing.MAX_GAP_H)
    if m_eps and o_eps:
        event    = "HIT"
        onset_dt = pairing.onset_offset_h(m_eps, o_eps)
    elif m_eps and not o_eps:
        event, onset_dt = "FA", None
    elif o_eps and not m_eps:
        event, onset_dt = "MISS", None
    else:
        event, onset_dt = "CN", None

    return {
        "event"      : event,
        "onset_dt_h" : onset_dt,          # модел − обс, часове (за HIT)
        "hourly"     : {"hits": hits, "misses": miss, "fa": fa, "cn": cn,
                        "POD": pod, "FAR": far, "CSI": csi,
                        "MAE_VIS": float(np.mean(mae_v)) if mae_v else None},
        "T"          : {"MAE": float(np.mean(mae_t)) if mae_t else None,
                        "Tmin_err": t_min_err,
                        "err_0306": float(np.mean(err_0306)) if err_0306 else None},
        "mod_min_vis": float(min(h["vis_sfc"] for h in history)),
    }


# ──────────────────────────────────────────────────────────────────────────
# Снимка на конфигурацията (за да помни всеки JSON с какви настройки е пуснат)
# ──────────────────────────────────────────────────────────────────────────
def config_snapshot():
    snap = {}
    try:
        import inspect, fog_model, run_case
        src = inspect.getsource(fog_model)
        m = re.search(r"RH_CRIT\s*=\s*([\d.]+)", src)
        if m: snap["RH_CRIT"] = float(m.group(1))
        dz = re.findall(r"DZ_EFF_SEB\s*=\s*([\d.]+)", src)
        if dz: snap["DZ_EFF_values"] = [float(x) for x in dz]
        m = re.search(r"max_cool_val\s*=\s*([^\n]+)", src)
        if m: snap["max_cool"] = m.group(1).strip()
        snap["fog_model_md5"] = hashlib.md5(src.encode()).hexdigest()[:10]
        snap["AIRPORT_CONFIG"] = run_case.AIRPORT_CONFIG
    except Exception as e:
        snap["error"] = str(e)
    return snap


# ──────────────────────────────────────────────────────────────────────────
# Регресионен гейт
# ──────────────────────────────────────────────────────────────────────────
_BASE_VER_RE = re.compile(r"^(LB[A-Z]{2})-v(\d+)$")


def select_active(names):
    """Разделя имената на бази на (активни, архивни).

    Активна = базата с най-висок номер за дадено летище (LBSF-v5 бие
    LBSF-v4). Неверсионирани имена (LBSF-pre-calib, LBSF-stage1) са
    архивни, АКО летището има поне една версионирана база; иначе
    остават активни, за да не остане летището без гейт.

    Причина (26.07.2026): сравнението срещу всички бази раждаше ~19
    регресии на пуск, всичките срещу надживени бази. Гейт, който вика
    'вълк' всеки път, спира да се чете.
    """
    best, unversioned = {}, {}
    for n in names:
        m = _BASE_VER_RE.match(n)
        if m:
            icao, ver = m.group(1), int(m.group(2))
            if icao not in best or ver > best[icao][1]:
                best[icao] = (n, ver)
        else:
            unversioned.setdefault(n.split("-")[0], []).append(n)
    active = {v[0] for v in best.values()}
    for icao, lst in unversioned.items():
        if icao not in best:
            active.update(lst)
    return active, set(names) - active


def load_baselines(active_only=True):
    """Връща (бази, имена_на_архивните)."""
    all_bases = {}
    for p in glob.glob(os.path.join(BASELINE_DIR, "*.json")):
        with open(p, encoding="utf-8") as f:
            all_bases[os.path.splitext(os.path.basename(p))[0]] = json.load(f)
    if not active_only:
        return all_bases, set()
    active, archived = select_active(all_bases.keys())
    return {k: v for k, v in all_bases.items() if k in active}, archived

EVENT_RANK = {"HIT": 3, "CN": 3, "FA": 1, "MISS": 0}

def check_regressions(results, baselines, strict_missing=True):
    """Сравнява текущите резултати с приетите бази.

    Връща (регресии, липсващи). Липсващ = случай, който е в базата,
    летището му е било в пуска, но случаят не се е изпълнил — най-често
    защото файлът е изчезнал от cases/. Старият код го подминаваше
    мълчаливо, което вдигаше агрегатните метрики без нищо да е поправено
    (LBWN_CDRY_2024-09-23, 26.07.2026).

    strict_missing=False при --category/--date: там непълнотата е
    очаквана и не е дефект.
    """
    regs, missing = [], []
    # Изключените (валежни) случаи не се съдят срещу базата: техният
    # изход не е физически заслужен в нито едната посока. НО те СЕ
    # ИЗПЪЛНИХА — не са липсващи. Държим ги в отделно множество, за да
    # не вдига гейтът фалшива тревога "провери cases/".
    cur = {r["case_id"]: r for r in results
           if "error" not in r and not r.get("excluded")}
    excluded_ids = {r["case_id"] for r in results if r.get("excluded")}
    ran_icaos = {r["icao"] for r in results}
    # Случаи, които СЕ ИЗПЪЛНИХА и паднаха. Не са "липсващи" — те са
    # провал. Старият код ги изключваше от cur и гейтът обявяваше
    # "без регресии", защото нямаше какво да сравни (31.07.2026:
    # verify_cases внасяше rh_crit_for от върнат run_case → нула
    # изпълнени случая → зелена присъда).
    errored = sorted(r["case_id"] for r in results if "error" in r)
    excl_in_base = []
    for bname, base in baselines.items():
        for cid, b in base.get("cases", {}).items():
            r = cur.get(cid)
            if r is None:
                if cid in excluded_ids:
                    # Изпълни се, но е изключен като валежен. Това е
                    # съзнателно решение, не липса.
                    excl_in_base.append(f"{bname}: {cid}")
                elif strict_missing and cid.split("_")[0] in ran_icaos:
                    missing.append(f"{bname}: {cid}")
                continue
            # 1) събитийно влошаване
            if EVENT_RANK[r["eval"]["event"]] < EVENT_RANK[b["event"]]:
                regs.append(f"{bname}: {cid}  събитие {b['event']} → "
                            f"{r['eval']['event']}")
            # 2) T влошаване
            bt, rt = b.get("T_MAE"), r["eval"]["T"]["MAE"]
            if bt is not None and rt is not None and rt > bt + 0.7:
                regs.append(f"{bname}: {cid}  MAE_T {bt:.2f} → {rt:.2f}")
    return (regs, sorted(set(missing)), errored,
            sorted(set(excl_in_base)))


def save_baseline(name, results):
    os.makedirs(BASELINE_DIR, exist_ok=True)
    cases = {}
    for r in results:
        if "error" in r or r.get("excluded"):
            continue
        cases[r["case_id"]] = {"event": r["eval"]["event"],
                               "T_MAE": r["eval"]["T"]["MAE"],
                               "csi_hourly": r["eval"]["hourly"]["CSI"]}
    payload = {"accepted_utc": datetime.now(timezone.utc).isoformat(),
               "config": config_snapshot(), "cases": cases}
    path = os.path.join(BASELINE_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[БАЗА] Приета: {path}  ({len(cases)} случая)")


# ──────────────────────────────────────────────────────────────────────────
# Отчет
# ──────────────────────────────────────────────────────────────────────────
def stage_report(results):
    """Оценка по етапи, по летище."""
    lines = []
    by_ap = {}
    for r in results:
        if "error" in r or r.get("excluded"):
            continue
        by_ap.setdefault(r["icao"], []).append(r)

    for icao in sorted(by_ap):
        rs = by_ap[icao]
        lines.append(f"\n### {icao}")
        # Етап 1 — CDRY: T критерии
        cdry = [r for r in rs if r["category"] == "CDRY"]
        if cdry:
            ok = 0
            for r in cdry:
                t = r["eval"]["T"]
                p1 = t["Tmin_err"] is not None and abs(t["Tmin_err"]) < STAGE1_TMIN_ERR
                p2 = t["err_0306"] is not None and t["err_0306"] < STAGE1_ERR_0306
                fa_ev = r["eval"]["event"] in ("FA",)
                ok += p1 and p2
                lines.append(
                    f"  Етап1 {r['date']}: Tmin_err="
                    f"{t['Tmin_err']:+.1f}°C{'✓' if p1 else '✗'} "
                    f"err03-06={t['err_0306'] if t['err_0306'] is not None else float('nan'):.1f}"
                    f"°C{'✓' if p2 else '✗'} "
                    f"{'⚠FA-епизод!' if fa_ev else ''}")
            lines.append(f"  Етап1 (T верига): {ok}/{len(cdry)} случая минават "
                         f"критерия {'— ГОТОВО ✓' if ok == len(cdry) else ''}")
        # Етап 2 — CFOG: събитийни hits + FA на сухите
        cfog = [r for r in rs if r["category"] == "CFOG"]
        if cfog:
            h = sum(r["eval"]["event"] == "HIT" for r in cfog)
            fa_dry = sum(r["eval"]["event"] == "FA"
                         for r in rs if r["category"] == "CDRY")
            onsets = [r["eval"]["onset_dt_h"] for r in cfog
                      if r["eval"]["onset_dt_h"] is not None]
            ons = f"  ср.Δt(начало)={np.mean(onsets):+.1f}h" if onsets else ""
            lines.append(f"  Етап2 (мъгла): HIT {h}/{len(cfog)}; "
                         f"FA на сухите: {fa_dry}{ons} "
                         f"{'— ГОТОВО ✓' if h == len(cfog) and fa_dry <= 1 else ''}")
        # Етап 3 — CLDY
        cldy = [r for r in rs if r["category"] == "CLDY"]
        if cldy:
            maes = [r["eval"]["T"]["MAE"] for r in cldy
                    if r["eval"]["T"]["MAE"] is not None]
            evs = ",".join(r["eval"]["event"] for r in cldy)
            lines.append(f"  Етап3 (облачност): ср.MAE_T="
                         f"{np.mean(maes):.1f}°C; събития: {evs}"
                         if maes else "  Етап3: няма T данни")
        # Етап 4 — DYNM: разпознат ли е режимът
        dynm = [r for r in rs if r["category"] == "DYNM"]
        if dynm:
            okr = sum(1 for r in dynm
                      if r["regime_log"] and
                      r["regime_log"][0]["regime"] == "dynamic")
            lines.append(f"  Етап4 (режими): DYNAMIC разпознат при старт "
                         f"{okr}/{len(dynm)}")
    return "\n".join(lines)


def matrix_report(results):
    lines = [f"\n{'':6}" + "".join(f"{c:>20}" for c in CATEGORIES)]
    by = {}
    for r in results:
        if "error" in r:
            continue
        # Изключените се броят отделно като E — не влизат в H/M/F/C.
        tag = "EXCL" if r.get("excluded") else r["eval"]["event"]
        by.setdefault(r["icao"], {}).setdefault(r["category"], []).append(tag)
    for icao in sorted(by):
        row = f"{icao:6}"
        for c in CATEGORIES:
            evs = by[icao].get(c, [])
            if not evs:
                row += f"{'—':>20}"
            else:
                s = f"H{evs.count('HIT')}/M{evs.count('MISS')}" \
                    f"/F{evs.count('FA')}/C{evs.count('CN')}"
                n_ex = evs.count("EXCL")
                if n_ex:
                    s += f"/E{n_ex}"
                row += f"{s:>20}"
        lines.append(row)
    return "\n".join(lines)


def excluded_report(results):
    """Кои случаи са изключени и защо."""
    ex = [r for r in results if r.get("excluded")]
    if not ex:
        return "  Няма изключени случаи."
    lines = [f"  {len(ex)} случая, изключени от метриката "
             f"(ниската видимост в наблюдението не е от мъгла):"]
    for r in sorted(ex, key=lambda x: x["case_id"]):
        causes = ", ".join(f"{k}={v}" for k, v in
                           sorted(r.get("obs_vis_cause", {}).items(),
                                  key=lambda kv: -kv[1]))
        lines.append(f"    {r['case_id']:<28} {r['excluded_reason']:<18} "
                     f"({causes})   [щеше да е {r['eval']['event']}]")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Верификация върху локални ситуации")
    ap.add_argument("--airport")
    ap.add_argument("--category", choices=CATEGORIES)
    ap.add_argument("--date")
    ap.add_argument("--hour", type=int, default=START_HOUR)
    ap.add_argument("--cases-dir", default=CASES_DIR)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--accept", metavar="ИМЕ",
                    help="Приеми текущия резултат като база (пр. LBSF-stage1)")
    ap.add_argument("--all-baselines", action="store_true",
                    help="Сравнявай и с архивните бази (старо поведение)")
    ap.add_argument("--selftest", action="store_true",
                    help="Приемателни тестове за класификатора на "
                         "валежните случаи; нула пускания на модела")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest_obs_cause())

    files = sorted(glob.glob(os.path.join(args.cases_dir, "LB??_*_*.txt")))
    cases = []
    for p in files:
        try:
            icao, cat, date_str, obs = load_case_file(p)
        except Exception as e:
            print(f"[!] Пропускам {p}: {e}")
            continue
        if args.airport and icao != args.airport.upper():   continue
        if args.category and cat != args.category:          continue
        if args.date and date_str != args.date:             continue
        cases.append((icao, cat, date_str, obs, p))

    if args.list:
        for icao, cat, d, obs, p in cases:
            print(f"{icao} {cat} {d}  ({len(obs)} METAR-а)  {p}")
        print(f"\nОбщо: {len(cases)}")
        return
    if not cases:
        print("Няма ситуации — провери cases/ и филтрите.")
        return

    baselines, archived = load_baselines(active_only=not args.all_baselines)
    results = []
    for i, (icao, cat, date_str, obs, path) in enumerate(cases, 1):
        cid = f"{icao}_{cat}_{date_str}"
        print(f"[{i}/{len(cases)}] {cid} ...", end=" ", flush=True)
        try:
            t0 = time.time()
            history, regime_log = run_model(icao, date_str, args.hour, obs)
            ev = evaluate(history, obs, args.hour, date_str)
            print(f"{ev['event']:4}  MAE_T="
                  f"{ev['T']['MAE'] if ev['T']['MAE'] is not None else float('nan'):.1f}°C  "
                  f"minVIS={ev['mod_min_vis']:.0f}m  ({time.time()-t0:.0f}s)")
            excl, reason, causes = diagnose_obs_cause(obs)
            if excl:
                print(f"      [EXCL] {reason} — ниската видимост в "
                      f"наблюдението е от валеж, не от мъгла")
            results.append({"case_id": cid, "icao": icao, "category": cat,
                            "date": date_str, "eval": ev,
                            "regime_log": regime_log,
                            "excluded": excl,
                            "excluded_reason": reason,
                            "obs_vis_cause": causes})
        except Exception as e:
            print(f"ГРЕШКА: {e}")
            results.append({"case_id": cid, "icao": icao, "category": cat,
                            "date": date_str, "error": str(e)})

    # Регресии срещу приетите бази
    regs, missing, errored, excl_in_base = check_regressions(
        results, baselines,
        strict_missing=not (args.category or args.date))
    n_ok = sum(1 for r in results if "error" not in r)
    n_excl = sum(1 for r in results if r.get("excluded"))
    scored = [r for r in results
              if "error" not in r and not r.get("excluded")]

    # Отчет
    print("\n" + "=" * 64)
    print("МАТРИЦА летище × категория (събитийно H/M/F/C, E=изключени):")
    print(matrix_report(results))

    # Агрегат — САМО върху оценяваните случаи
    _h = sum(r["eval"]["event"] == "HIT"  for r in scored)
    _m = sum(r["eval"]["event"] == "MISS" for r in scored)
    _f = sum(r["eval"]["event"] == "FA"   for r in scored)
    _c = sum(r["eval"]["event"] == "CN"   for r in scored)
    _csi = _h / (_h + _m + _f) if (_h + _m + _f) else float("nan")
    _maes = [r["eval"]["T"]["MAE"] for r in scored
             if r["eval"]["T"]["MAE"] is not None]
    print(f"\nОБЩО (оценявани): HIT={_h} MISS={_m} FA={_f} CN={_c}   "
          f"CSI={_csi:.3f}   "
          f"MAE_T={np.mean(_maes):.3f} °C   {len(scored)} случая")
    if n_excl:
        print(f"       + {n_excl} изключени (валеж) — не участват в CSI")

    print("\nИЗКЛЮЧЕНИ СЛУЧАИ:")
    print(excluded_report(results))

    print("\nОЦЕНКА ПО ЕТАПИ:")
    print(stage_report(results))
    if baselines:
        print(f"\n[Гейт] Активни бази: {', '.join(sorted(baselines))}")
        if archived:
            print(f"[Гейт] Архивни (пропуснати): "
                  f"{', '.join(sorted(archived))}")
    if excl_in_base:
        print(f"\n[Гейт] {len(excl_in_base)} случая от базите са изключени "
              f"като валежни — не се съдят:")
        for c in excl_in_base:
            print("  ○ " + c)
        print("  → базите ще ги изпуснат при следващото --accept. Това е "
              "очаквано.")
    if missing:
        print("\n" + "!" * 64)
        print("ЛИПСВАЩИ СЛУЧАИ — в базата са, но не се изпълниха:")
        for m in missing:
            print("  ✗ " + m)
        print("  → провери cases/. Метриките НЕ са сравними с базата,")
        print("    докато наборът не бъде възстановен или базата преприета.")
        print("!" * 64)
    if errored:
        print("\n" + "!" * 64)
        print(f"ПАДНАЛИ СЛУЧАИ: {len(errored)} — изпълниха се и гръмнаха:")
        for c in errored[:20]:
            print("  ✗ " + c)
        if len(errored) > 20:
            print(f"  ... и още {len(errored) - 20}")
        print("  → пробегът НЕ е валиден за сравнение с базата.")
        print("!" * 64)
    if regs:
        print("\n" + "!" * 64)
        print("РЕГРЕСИИ спрямо приети бази:")
        for r in regs:
            print("  ⚠ " + r)
        print("!" * 64)
    if n_ok == 0:
        print("\n" + "!" * 64)
        print("НЕПЪЛЕН ПРОБЕГ — нула успешно изпълнени случая.")
        print("  Гейтът НЕ се произнася. Липсата на регресии тук значи")
        print("  само, че няма какво да се сравни.")
        print("!" * 64)
    elif baselines and not regs and not missing and not errored:
        print(f"[Гейт] Без регресии спрямо активните бази ✓  "
              f"({n_ok} случая)")

    # Запис
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out = {"run_utc": stamp, "config": config_snapshot(),
           "results": results, "regressions": regs,
           "missing_cases": missing,
           "excluded_in_baseline": excl_in_base,
           "errored_cases": errored,
           "n_evaluated": n_ok,
           "n_excluded": n_excl,
           "baselines_active": sorted(baselines),
           "baselines_archived": sorted(archived)}
    jpath = os.path.join(LOGS_DIR, f"verify_{stamp}.json")
    def _conv(o):
        if isinstance(o, np.integer):  return int(o)
        if isinstance(o, np.floating): return float(o)
        if isinstance(o, np.ndarray):  return o.tolist()
        raise TypeError(type(o))
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=_conv)
    print(f"\n[OK] JSON: {jpath}")

    if args.accept:
        save_baseline(args.accept, results)


if __name__ == "__main__":
    main()
