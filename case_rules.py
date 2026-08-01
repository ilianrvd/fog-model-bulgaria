# -*- coding: utf-8 -*-
"""
case_rules.py — разбор на METAR и категоризация на случаи
==========================================================
Единствен източник за:
  * разбора на суровите METAR-и (видимост, явление, облачност, вятър, T/Td)
  * статистиките в хедъра на case файла
  * правилата за категория CFOG / CDRY / CLDY / DYNM

Импортира се от make_case.py и relabel_cases.py. Не пипа модела.

Защо нови правила (30.07.2026)
------------------------------
Старите хедъри са смятани по КРЪГЛИ ЧАСОВЕ. Доказателство:
LBGO_CDRY_2025-01-25 твърди "Мин. видимост: 6000 m" и "METAR-и с
VIS < 2000 m: не", а в 05:30 има `0450 0400W R09/0600D FZFG` — 450 m
замръзваща мъгла. 6000 е минимумът само по кръглите часове.

Второ: критерият за мъгла е бил само по видимост. Регексът `\\bFG\\b`
в parse_metar_light дава НУЛА попадения при 34 METAR-а с 21 мъглени,
защото `\\b` пада заради "Z" в FZFG и "C" в BCFG.

Нови правила:
  * всички METAR-и, не само кръглите часове
  * мъглата се разпознава И по явление (FG/FZFG/BCFG/MIFG/PRFG),
    И по видимост
  * DYNM и CLDY се определят по вятър, валеж и НИСКА облачност
    (високият цирус не пречи на радиационното охлаждане)
"""
import re
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

# ── Прагове ───────────────────────────────────────────────────
NIGHT_START_UTC = 16      # включващо
NIGHT_END_UTC   = 9       # включващо, следващия ден

# ЯДРЕНА нощ — прозорецът, в който се преценява динамичността.
# Пълният 16–09 хваща вечерния бриз и сутрешното усилване; лятото това
# праща тихи радиационни нощи в DYNM. Измерено на LBWN 2026-07-21:
# вятър по часове 4,3,4,3,4,3,2,3,1,0,1,0,2,8,11,13 kt — максимумът 13
# е в 09 UTC, а през нощта е 0–4. Зимата разликата не личи, защото
# вятърът е постоянен, затова 264-те случая не я показаха.
CORE_START_UTC  = 20
CORE_END_UTC    = 6

# Праговете НЕ са съчинени — извлечени са от 264-те съществуващи етикета
# с infer_rules.py (30.07.2026). Двете разделяния са 100 % точни:
#   вятър:    DYNM ≥ 8 kt срещу CDRY < 8 kt   (CFOG/CDRY max 6, DYNM min 8)
#   облаци:   CLDY ≥ 0.53 срещу CDRY < 0.53   (CLDY min 0.5, CDRY max 0.3)
# Първият опит ползваше DYNM_WIND_KT = 15 и правило за валеж — съчинени,
# и преетикетираха 100 от 264 случая по осите CLDY/DYNM/CDRY.
FOG_VIS      = 1000.0     # m — "мъгла" по видимост
LOWVIS_VIS   = 2000.0     # m — праг на събитийната метрика
FOG_MIN_N    = 2          # брой METAR-а за устойчива мъгла
FOG_DENSE_M  = 500.0      # m — плътна мъгла, стига и един METAR
FOG_WX_VIS   = 2000.0     # m — явлението се брои само при намалена видимост
DYNM_WIND_KT = 7.0        # ЯДРЕН вятър (20–06). Извлечен оптимум: 96.3 %
                          # разделяне DYNM/CDRY. Пълният прозорец дава
                          # 100 % при 8 kt, но праща тихи летни нощи в
                          # DYNM заради бриза. Разминаването (~5 зимни
                          # нощи, чийто вятър е затихнал в ядрото) не
                          # засяга съществуващия набор: classify_preserving
                          # не пипа оста вятър.
CLDY_FRAC    = 0.50       # извлечено, 100 % разделяне срещу CDRY
LOWCLOUD_FT  = 10000      # ниска/средна облачност: база под това

_METAR_RE = re.compile(r"^(?:METAR|SPECI)\s+(LB[A-Z]{2})\s+(\d{2})(\d{2})(\d{2})Z")
_WIND_RE  = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b")
_VIS_RE   = re.compile(r"\s(\d{4})(?:NDV)?\s")
_TT_RE    = re.compile(r"\s(M?\d{2})/(M?\d{2})\s")
_CLD_RE   = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})")
_VV_RE    = re.compile(r"\bVV(\d{3})\b")

# Явления. Редът има значение — по-дългите първи.
FOG_WX    = ("FZFG", "BCFG", "MIFG", "PRFG", "FG")
PRECIP_WX = ("TS", "SHRA", "SHSN", "RA", "SN", "DZ", "GR", "GS", "PL")


def _t2f(s):
    return -float(s[1:]) if s.startswith("M") else float(s)


def _has_wx(raw, codes):
    """Явление в тялото на METAR-а, БЕЗ TEMPO/BECMG частта."""
    body = re.split(r"\b(?:TEMPO|BECMG|NOSIG)\b", raw)[0]
    for c in codes:
        # граница отляво: начало или интервал/знак за интензитет
        if re.search(r"(?<![A-Z])[-+]?" + c + r"\b", body):
            return c
    return None


def parse_metar(raw, base_date):
    """
    Разбор на един METAR. Връща dict или None.

    base_date : 'YYYY-MM-DD' — за да се разреши денят от DDHHMMZ.
    """
    raw = raw.strip()
    m = _METAR_RE.match(raw)
    if not m:
        return None
    icao, day, hh, mm = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    d0 = datetime.strptime(base_date, "%Y-%m-%d")
    dt = None
    for cand in (d0, d0 + timedelta(days=1), d0 - timedelta(days=1)):
        if cand.day == day:
            dt = cand.replace(hour=hh, minute=mm, tzinfo=UTC)
            break
    if dt is None:
        return None

    body = re.split(r"\b(?:TEMPO|BECMG)\b", raw)[0]

    # вятър
    wind_kt = gust_kt = None
    wm = _WIND_RE.search(body)
    if wm:
        wind_kt = float(wm.group(2))
        gust_kt = float(wm.group(3)) if wm.group(3) else None

    # видимост
    if "CAVOK" in body:
        vis = 10000.0
    else:
        vm = _VIS_RE.search(body)
        vis = float(vm.group(1)) if vm else None
        if vis is not None and vis >= 9999:
            vis = 10000.0

    # T / Td
    T = Td = None
    tm = _TT_RE.search(body)
    if tm:
        T, Td = _t2f(tm.group(1)), _t2f(tm.group(2))

    # явления
    fog_wx    = _has_wx(raw, FOG_WX)
    precip_wx = _has_wx(raw, PRECIP_WX)
    mist      = bool(_has_wx(raw, ("BR",)))

    # облачност — най-ниската BKN/OVC база [ft]
    low_ovc = None
    for grp, hh3 in _CLD_RE.findall(body):
        if grp in ("BKN", "OVC"):
            ft = int(hh3) * 100
            if ft <= LOWCLOUD_FT and (low_ovc is None or ft < low_ovc):
                low_ovc = ft
    vv = _VV_RE.search(body)
    if vv:
        low_ovc = min(low_ovc or 99999, int(vv.group(1)) * 100)

    return {
        "dt": dt, "icao": icao, "raw": raw,
        "vis": vis, "T": T, "Td": Td,
        "wind_kt": wind_kt, "gust_kt": gust_kt,
        "fog_wx": fog_wx, "precip_wx": precip_wx, "mist": mist,
        "low_ovc_ft": low_ovc,
    }


def night_window(date_str):
    """(начало, край) на нощта за дадена дата."""
    d0 = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return (d0.replace(hour=NIGHT_START_UTC),
            d0 + timedelta(days=1, hours=NIGHT_END_UTC))


def in_core(dt):
    """Ядрената нощ: 20:00 → 06:00 UTC."""
    return dt.hour >= CORE_START_UTC or dt.hour <= CORE_END_UTC


def stats(obs):
    """Статистики по СПИСЪК от разборени METAR-и (вече филтрирани по нощ)."""
    n = len(obs)
    core = [o for o in obs if in_core(o["dt"])]
    w_core = [o["wind_kt"] for o in core if o["wind_kt"] is not None]
    vis  = [o["vis"] for o in obs if o["vis"] is not None]
    wind = [o["wind_kt"] for o in obs if o["wind_kt"] is not None]
    gust = [o["gust_kt"] for o in obs if o["gust_kt"] is not None]
    spread = [o["T"] - o["Td"] for o in obs
              if o["T"] is not None and o["Td"] is not None]

    n_fogwx  = sum(1 for o in obs if o["fog_wx"])
    n_fogvis = sum(1 for o in vis_iter(obs) if o < FOG_VIS)
    n_lowvis = sum(1 for o in vis_iter(obs) if o < LOWVIS_VIS)
    n_precip = sum(1 for o in obs if o["precip_wx"])
    n_lowovc = sum(1 for o in obs if o["low_ovc_ft"] is not None)
    n_clear  = n - n_lowovc

    # мъглени моменти: явление ИЛИ ниска видимост
    fog_idx = [i for i, o in enumerate(obs)
               if o["fog_wx"] or (o["vis"] is not None and o["vis"] < FOG_VIS)]

    # медианна стъпка [h] — за "приблизителни часове мъгла"
    step_h = 0.5
    if n >= 2:
        d = sorted((obs[i + 1]["dt"] - obs[i]["dt"]).total_seconds() / 3600.0
                   for i in range(n - 1))
        step_h = d[len(d) // 2]

    clear_before_fog = None
    if fog_idx:
        pre = obs[:fog_idx[0]]
        if pre:
            clear_before_fog = 100.0 * sum(
                1 for o in pre if o["low_ovc_ft"] is None) / len(pre)

    return {
        "n": n, "n_core": len(core),
        "wind_mean": (sum(wind) / len(wind)) if wind else 0.0,
        "wind_max": max(wind) if wind else 0.0,
        "wind_max_core": max(w_core) if w_core else 0.0,
        "wind_mean_core": (sum(w_core) / len(w_core)) if w_core else 0.0,
        "gust_max": max(gust) if gust else None,
        "spread_min": min(spread) if spread else None,
        "vis_min": min(vis) if vis else None,
        "n_fogwx": n_fogwx, "n_fogvis": n_fogvis, "n_lowvis": n_lowvis,
        "n_precip": n_precip, "n_lowovc": n_lowovc,
        "clear_pct": (100.0 * n_clear / n) if n else 0.0,
        "n_fog": len(fog_idx),
        "fog_hours": len(fog_idx) * step_h,
        "clear_before_fog": clear_before_fog,
        "step_h": step_h,
    }


def vis_iter(obs):
    return [o["vis"] for o in obs if o["vis"] is not None]


def is_fog(st):
    """
    Мъгла ли е нощта. ТОВА е единственото правило, което се променя
    спрямо оригиналния набор — останалите оси са възпроизведени.

    Три пътя, всеки проверен срещу 43-те съществуващи CFOG случая:
      1. >= 2 METAR-а под 1000 m            — устойчива мъгла по видимост
      2. минимална видимост < 500 m         — плътна, стига и един момент
      3. >= 2 METAR-а с явление И vis < 2000 — AUTO сензорът мята
         преобладаващата видимост, докато FZFG върви непрекъснато
         (LBGO 2024-12-30: 19 явления срещу 14 METAR-а под 1000 m)

    Условието vis < 2000 в път 3 не е излишно: без него BCFG/MIFG при
    преобладаваща 3200 и 4100 m биха станали CFOG, а моделът прогнозира
    преобладаваща видимост.

    Задържа 42 от 43-те CFOG. Единственият отпаднал —
    LBWN_CFOG_2024-10-21 — има минимум 2100 m и нула явления.
    """
    vmin = st["vis_min"] if st["vis_min"] is not None else 10000.0
    return (st["n_fogvis"] >= FOG_MIN_N
            or vmin < FOG_DENSE_M
            or (st["n_fogwx"] >= FOG_MIN_N and vmin < FOG_WX_VIS))


def classify(st):
    """
    Категория по статистиките. Редът е приоритетен.

    CFOG — виж is_fog().
    CLDY — ниска BKN/OVC в поне CLDY_FRAC от нощта. Проверява се ПРЕДИ
           DYNM: облачните нощи имат вятър от 3 до 26 kt и проверката
           по вятър първа ги отнася масово в DYNM.
    DYNM — вятър >= DYNM_WIND_KT в ЯДРЕНАТА нощ (20–06) при
           ясно/малооблачно. Не по целия прозорец: 16–09 включва
           вечерния бриз и сутрешното усилване.
    CDRY — ясна тиха нощ без мъгла.

    ВНИМАНИЕ: границата CLDY/DYNM е несигурна. Разделянето по вятър
    между тях е само 68.5 %, а DYNM има ниска облачност до 0.7 — тоест
    оригиналната категоризация вероятно е ползвала синоптични сигнали
    от ICON (ΔT850, ΔV), които тук ги няма. За СЪЩЕСТВУВАЩИ случаи
    ползвай classify_preserving(); това правило е за нови.
    """
    if is_fog(st):
        return "CFOG"
    if st["n"] and (st["n_lowovc"] / st["n"]) >= CLDY_FRAC:
        return "CLDY"
    if st.get("wind_max_core", st["wind_max"]) >= DYNM_WIND_KT:
        return "DYNM"
    return "CDRY"


def classify_preserving(st, current_cat):
    """
    Минимална намеса за СЪЩЕСТВУВАЩИ случаи: мени се само по оста мъгла.

    Ако нощта е мъглива → CFOG. Ако не е, но е етикетирана CFOG →
    преминава по останалите правила. Иначе етикетът се ЗАПАЗВА —
    границите CLDY/DYNM/CDRY не се пипат, защото не са дефектни.
    """
    if is_fog(st):
        return "CFOG"
    if current_cat == "CFOG":
        return classify(st)
    return current_cat


def header(icao, cat, date_str, st):
    """Хедър в СЪЩИЯ формат като съществуващите case файлове."""
    d0 = datetime.strptime(date_str, "%Y-%m-%d")
    d1 = d0 + timedelta(days=1)
    L = []
    A = L.append
    A("=" * 64)
    A(f"ЛЕТИЩЕ    : {icao}")
    A(f"КАТЕГОРИЯ : {cat}")
    A(f"НОЩ       : {date_str} / {d1:%Y-%m-%d} "
      f"(UTC {NIGHT_START_UTC:02d}:00–{NIGHT_END_UTC:02d}:00)")
    A("-" * 64)
    A(f"Общо METAR-и в нощта  : {st['n']}")
    A(f"Среден вятър           : {st['wind_mean']:.1f} kt")
    A(f"Максимален вятър       : {st['wind_max']:.1f} kt")
    A(f"Мин. T−Td спред        : "
      f"{'—' if st['spread_min'] is None else f'{st_spread(st):.1f} °C'}")
    A(f"Мин. видимост          : "
      f"{'—' if st['vis_min'] is None else f'{st_vis(st)} m'}")

    if cat == "CFOG":
        A(f"METAR-и с мъгла        : {st['n_fogvis']}")
        A(f"METAR-и с явление      : {st['n_fogwx']}")
        A(f"Прибл. часове мъгла    : {st['fog_hours']:.1f} ч")
        A(f"Ясни METAR-и преди мъгла: "
          f"{'—' if st['clear_before_fog'] is None else f'{st_cbf(st):.0f}%'}")
        A(f"Значим валеж           : {'да' if st['n_precip'] else 'не'}")
    elif cat == "DYNM":
        A(f"Ясни METAR-и (нощта)   : {st['clear_pct']:.0f}%")
        A(f"METAR-и с валеж        : {st['n_precip']}")
        A(f"Максимален порив       : "
          f"{'—' if st['gust_max'] is None else f'{st_gust(st):.0f} kt'}")
        A(f"METAR-и с VIS < 2000 m : {'да' if st['n_lowvis'] else 'не'}")
    elif cat == "CLDY":
        A(f"Ясни METAR-и (нощта)   : {st['clear_pct']:.0f}%")
        A(f"METAR-и с ниска BKN/OVC: {st['n_lowovc']}")
        A(f"METAR-и с VIS < 2000 m : {'да' if st['n_lowvis'] else 'не'}")
    else:  # CDRY
        A(f"Ясни METAR-и (нощта)   : {st['clear_pct']:.0f}%")
        A(f"METAR-и с VIS < 2000 m : {'да' if st['n_lowvis'] else 'не'}")

    A("=" * 64)
    A("СУРОВИ METAR-И:")
    A("-" * 64)
    return "\n".join(L)


# помощни, за да не се чупи f-string вложеността
def st_spread(st): return st["spread_min"]
def st_vis(st):    return int(round(st["vis_min"]))
def st_cbf(st):    return st["clear_before_fog"]
def st_gust(st):   return st["gust_max"]


def build_case_text(icao, cat, date_str, obs):
    """Пълен текст на case файл: хедър + сурови METAR-и."""
    st = stats(obs)
    lines = [header(icao, cat, date_str, st)]
    lines.extend(o["raw"] for o in obs)
    return "\n".join(lines) + "\n"


def load_raw_metars(path):
    """Изважда суровите METAR редове от съществуващ case файл."""
    out = []
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if s.startswith("METAR LB") or s.startswith("SPECI LB"):
            out.append(s)
    return out
