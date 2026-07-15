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
EVENT_MIN_HRS      = 2  # МОДЕЛЕН епизод: мин. последователни часове (шумов филтър)
EVENT_MIN_HRS_OBS  = 1  # НАБЛЮДАВАН епизод: 1 час стига — кратката реална
                        # мъгла (напр. 21.10: само 04h VIS=200m) Е събитие
EVENT_END_UTC      = 7  # КАРАНТИНА: часове след 06 UTC не влизат в събитийната
                        # метрика — изгревният преход (07-08h) има известен бъг
                        # (T колапс при RADIATIVE→DYNAMIC на изгрев, 10-05 случая),
                        # който прави фалшива мъгла СЛЕД нощта. Нощната физика
                        # се оценява чисто; изгревът — отделна сесия.
HOURLY_VIS    = 1000.0  # праг на часовата (диагностична) метрика [m]

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

    model = FogModel1D(z_model, T_m, qv_m, p_m, u_m, v_m,
                       hour0=float(hour), dt=60, day_of_year=doy)
    model.cc_series = profile.get("cc_series", [])

    T_soil_icon = profile.get("T_soil")
    if T_soil_icon is not None:
        model.T_soil = float(T_soil_icon)
        model.T_skin = min(float(T_soil_icon), model.T[0])

    ql_init_raw = profile.get("ql_init")
    if ql_init_raw is not None and len(ql_init_raw) == len(profile["z"]):
        model.ql = np.interp(z_model, profile["z"], ql_init_raw)
    elif ql_init_raw is not None and np.any(np.array(ql_init_raw) > 0):
        model.ql = np.where(z_model < 50, float(np.max(ql_init_raw)), 0.0)
    else:
        model.ql = np.zeros(len(z_model))

    hourly_profs = profile.get("hourly_profiles", [])
    steps_total, steps_per_hr, dt = FORECAST_H * 60, 60, 60

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
            _old = sys.stdout; sys.stdout = _io.StringIO()
            cand_regime, cand_tau, cand_reason = diagnose_regime(
                {"hourly_profiles": remaining}, {}, cfg)
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
        if step % steps_per_hr == 0:
            model.diagnose()

    return model.history, regime_log


# ──────────────────────────────────────────────────────────────────────────
# Оценка
# ──────────────────────────────────────────────────────────────────────────
def _hourly_pairs(history, obs_list, hour, date_str):
    """Сдвоява прогнозни цели часове с най-близък METAR (±30 мин)."""
    t0 = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, tzinfo=timezone.utc)
    pairs = []
    for h in history:
        he = h.get("time_h")
        if he is None:
            # реконструирай от hour_utc спрямо старта
            he = (h["hour_utc"] - hour) % 24
        t = t0 + timedelta(hours=float(he))
        best, bdt = None, 1801
        for o in obs_list:
            d = abs((o["dt"] - t).total_seconds())
            if d < bdt:
                best, bdt = o, d
        pairs.append((t, h, best))
    return pairs


def _episodes(series_bool, min_len):
    """[(start_idx, end_idx)] на последователности от True с дължина>=min_len."""
    eps, s = [], None
    for i, v in enumerate(series_bool + [False]):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= min_len:
                eps.append((s, i - 1))
            s = None
    return eps


def evaluate(history, obs_list, hour, date_str):
    pairs = _hourly_pairs(history, obs_list, hour, date_str)

    # ── часова метрика (праг 1000m) + T метрики
    hits = miss = fa = cn = 0
    mae_v, mae_t, err_0306 = [], [], []
    tmods, tobs_l = [], []
    mod_ev, obs_ev, hrs = [], [], []
    for t, h, o in pairs:
        vm = float(h["vis_sfc"])
        tm = float(h["T_sfc"]) - 273.15
        hrs.append(t.hour)
        mod_ev.append(vm < EVENT_VIS)
        tmods.append(tm)
        if o is None or o["vis"] is None:
            obs_ev.append(False)
            tobs_l.append(None)
            continue
        vo = o["vis"]
        obs_ev.append(vo < EVENT_VIS)
        mae_v.append(abs(vm - vo))
        pm, po = vm < HOURLY_VIS, vo < HOURLY_VIS
        if pm and po:       hits += 1
        elif pm and not po: fa   += 1
        elif po and not pm: miss += 1
        else:               cn   += 1
        if o["T"] is not None:
            tobs_l.append(o["T"])
            mae_t.append(abs(tm - o["T"]))
            if 3 <= t.hour <= 6:
                err_0306.append(abs(tm - o["T"]))
        else:
            tobs_l.append(None)

    pod = hits / (hits + miss) if (hits + miss) else None
    far = fa / (hits + fa)     if (hits + fa)   else None
    csi = hits / (hits + miss + fa) if (hits + miss + fa) else None

    # T_min грешка
    t_min_err = None
    obs_t = [x for x in tobs_l if x is not None]
    if obs_t and tmods:
        t_min_err = min(tmods) - min(obs_t)

    # ── събитийна метрика (само до EVENT_END_UTC — изгревна карантина)
    _in_win = [not (EVENT_END_UTC < hh < 16) for hh in hrs]
    mod_ev_w = [e and w for e, w in zip(mod_ev, _in_win)]
    obs_ev_w = [e and w for e, w in zip(obs_ev, _in_win)]
    m_eps = _episodes(mod_ev_w, EVENT_MIN_HRS)
    o_eps = _episodes(obs_ev_w, EVENT_MIN_HRS_OBS)
    if m_eps and o_eps:
        event = "HIT"
        onset_dt = float(m_eps[0][0] - o_eps[0][0])
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
def load_baselines():
    bases = {}
    for p in glob.glob(os.path.join(BASELINE_DIR, "*.json")):
        with open(p, encoding="utf-8") as f:
            bases[os.path.splitext(os.path.basename(p))[0]] = json.load(f)
    return bases

EVENT_RANK = {"HIT": 3, "CN": 3, "FA": 1, "MISS": 0}

def check_regressions(results, baselines):
    """Сравнява текущите резултати с всички приети бази."""
    regs = []
    cur = {r["case_id"]: r for r in results if "error" not in r}
    for bname, base in baselines.items():
        for cid, b in base.get("cases", {}).items():
            r = cur.get(cid)
            if r is None:
                continue
            # 1) събитийно влошаване
            if EVENT_RANK[r["eval"]["event"]] < EVENT_RANK[b["event"]]:
                regs.append(f"{bname}: {cid}  събитие {b['event']} → "
                            f"{r['eval']['event']}")
            # 2) T влошаване
            bt, rt = b.get("T_MAE"), r["eval"]["T"]["MAE"]
            if bt is not None and rt is not None and rt > bt + 0.7:
                regs.append(f"{bname}: {cid}  MAE_T {bt:.2f} → {rt:.2f}")
    return regs


def save_baseline(name, results):
    os.makedirs(BASELINE_DIR, exist_ok=True)
    cases = {}
    for r in results:
        if "error" in r:
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
        if "error" in r:
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
    lines = [f"\n{'':6}" + "".join(f"{c:>18}" for c in CATEGORIES)]
    by = {}
    for r in results:
        if "error" in r:
            continue
        by.setdefault(r["icao"], {}).setdefault(r["category"], []).append(
            r["eval"]["event"])
    for icao in sorted(by):
        row = f"{icao:6}"
        for c in CATEGORIES:
            evs = by[icao].get(c, [])
            if not evs:
                row += f"{'—':>18}"
            else:
                s = f"H{evs.count('HIT')}/M{evs.count('MISS')}" \
                    f"/F{evs.count('FA')}/C{evs.count('CN')}"
                row += f"{s:>18}"
        lines.append(row)
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
    args = ap.parse_args()

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

    baselines = load_baselines()
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
            results.append({"case_id": cid, "icao": icao, "category": cat,
                            "date": date_str, "eval": ev,
                            "regime_log": regime_log})
        except Exception as e:
            print(f"ГРЕШКА: {e}")
            results.append({"case_id": cid, "icao": icao, "category": cat,
                            "date": date_str, "error": str(e)})

    # Регресии срещу приетите бази
    regs = check_regressions(results, baselines)

    # Отчет
    print("\n" + "=" * 64)
    print("МАТРИЦА летище × категория (събитийно H/M/F/C):")
    print(matrix_report(results))
    print("\nОЦЕНКА ПО ЕТАПИ:")
    print(stage_report(results))
    if regs:
        print("\n" + "!" * 64)
        print("РЕГРЕСИИ спрямо приети бази:")
        for r in regs:
            print("  ⚠ " + r)
        print("!" * 64)
    else:
        if baselines:
            print("\n[Гейт] Без регресии спрямо приетите бази ✓")

    # Запис
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out = {"run_utc": stamp, "config": config_snapshot(),
           "results": results, "regressions": regs}
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
