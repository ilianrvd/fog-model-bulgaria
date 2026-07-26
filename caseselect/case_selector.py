#!/usr/bin/env python3
"""
case_selector.py — Подбор на тестови метеорологични ситуации от METAR архив
Проект: 1D модел за прогноза на мъгла за българските летища

Формат на входния файл (OGIMET/локален архив):
  LBBG 2024-01-10 00:00 SA   METAR LBBG 100000Z AUTO 01014KT 9999 OVC042/// M03/M10 Q1028 NOSIG=
  LBGO 2024-01-10 00:30 SA   METAR LBGO 100030Z AUTO 28004KT 9999 NCD M07/M09 Q1034=

Всеки METAR е на един ред; редовете могат да са смесени (различни летища).
Файлът се задава в METAR_FILES (речник ICAO → път).
"""

import os
import re
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

# ══════════════════════════════════════════════════════════════════
#  КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════

# Летища
AIRPORTS = ["LBSF", "LBWN", "LBBG", "LBPD", "LBGO"]

# Входни файлове — по един за всички летища или отделни.
# Ако един файл съдържа всички, посочете го за всяко летище.
# Ако имате отделни файлове: {"LBSF": "data/LBSF.txt", ...}
METAR_FILES = {
    "LBSF": "metar_data/metars.txt",
    "LBWN": "metar_data/metars.txt",
    "LBBG": "metar_data/metars.txt",
    "LBPD": "metar_data/metars.txt",
    "LBGO": "metar_data/metars.txt",
}

# Изходна директория
OUTPUT_DIR = "cases"

# ── Нощен прозорец (UTC) ──────────────────────────────────────────
NIGHT_START_HOUR = 16   # ден D, 16:00 UTC — начало
NIGHT_END_HOUR   = 9    # ден D+1, 09:00 UTC — край (изключително)

# ── Прагове: вятър ───────────────────────────────────────────────
WIND_MEAN_CALM   = 4    # kt — среден вятър за „тиха" нощ (CFOG/CDRY)
WIND_MAX_CALM    = 6    # kt — макс. вятър за „тиха" нощ
WIND_MEAN_DYNM   = 6    # kt — среден вятър за DYNM
WIND_STRONG_KT   = 10   # kt — „силен" METAR за DYNM
WIND_STRONG_CNT  = 3    # брой METAR-и с ≥ WIND_STRONG_KT за DYNM

# ── Прагове: облачност ────────────────────────────────────────────
CEIL_LOW_FT      = 10000  # ft — BKN/OVC под тази → „не ясно"
CLEAR_FRAC_MIN   = 0.70   # дял ясни METAR-и преди мъглата (CFOG/CDRY)
CLOUDY_FRAC_MIN  = 0.60   # дял BKN/OVC за CLDY

# ── Прагове: видимост / мъгла ─────────────────────────────────────
FOG_VIS_M        = 1000   # m — под тази → мъглив METAR
FOG_CNT_MIN      = 2      # мин. мъгливи METAR-и за CFOG
DRY_VIS_MIN_M    = 2000   # m — никой METAR под тази за CDRY

# ── Прагове: спред ────────────────────────────────────────────────
DRY_SPREAD_MAX   = 3.0    # °C — мин. T−Td ≤ 3 °C за CDRY

# ── Near-miss прагове ─────────────────────────────────────────────
NM_WIND_MEAN_MAX = 6.0    # kt — „почти тих" за near-miss
NM_SPREAD_MAX    = 5.0    # °C — „почти влажно" за near-miss

# ══════════════════════════════════════════════════════════════════
#  ПАРСЕР НА ВХОДНИЯ РЕД
# ══════════════════════════════════════════════════════════════════

# Префиксен ред: ICAO YYYY-MM-DD HH:MM SA   METAR ...=
_RE_PREFIX = re.compile(
    r"^([A-Z]{4})\s+"
    r"(\d{4}-\d{2}-\d{2})\s+"
    r"(\d{2}):(\d{2})\s+"
    r"\S+\s+"       # тип съобщение: SA, SP и т.н.
    r"(.+?)=?\s*$"  # суров METAR (без финалното =)
)

# Резервен шаблон — ред без префикс (само суров METAR)
_RE_RAW_ONLY = re.compile(r"^(?:METAR|SPECI)\s+[A-Z]{4}\s+\d{6}Z")

def parse_line(line: str) -> tuple[date | None, str | None]:
    """
    Парсира един ред от файла.
    Връща (дата_UTC, суров_METAR_стринг) или (None, None).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None

    m = _RE_PREFIX.match(line)
    if m:
        # Извличаме датата от префикса — надеждна реална дата
        dt_str  = m.group(2)            # YYYY-MM-DD
        raw     = m.group(5).strip()
        try:
            dt = date.fromisoformat(dt_str)
        except ValueError:
            return None, None
        # Уверяваме се, че raw започва с METAR/SPECI
        if not raw.startswith(("METAR", "SPECI")):
            raw = "METAR " + raw
        return dt, raw

    # Резервен път — ред само с METAR (без дата-префикс)
    if _RE_RAW_ONLY.match(line):
        return None, line   # дата неизвестна → ще се пропусне или обработи

    return None, None


# ══════════════════════════════════════════════════════════════════
#  METAR ПАРСЕР
# ══════════════════════════════════════════════════════════════════

_RE_WIND  = re.compile(r"\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b")
_RE_CLOUD = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})(?:///|CB|TCU)?\b")
_RE_TEMP  = re.compile(r"\b(M?\d{1,2})/(M?\d{1,2})\b")
_RE_VIS4  = re.compile(r"(?<!\d)(9999|\d{4})(?!\d)")   # 4-цифрена видимост

# Значими валежи (блокират CFOG/CDRY)
_SIG_PRECIP = frozenset({"RA", "SN", "GR", "GS", "RASN", "SNRA",
                          "SHRA", "SHSN", "SHGR", "TSGR", "TSRA", "TSSN"})
# Слаб ръмеж — допуска се само в самата мъгла
_LIGHT_DZ   = frozenset({"DZ"})


def _todeg(s: str) -> float:
    return -float(s[1:]) if s.startswith("M") else float(s)


def parse_metar(raw: str) -> dict | None:
    """
    Парсира суров METAR стринг.
    Връща речник с полетата или None при неразпознат формат.
    Устойчив на: AUTO, COR, CCA, VRB вятър, CAVOK, NSC, NCD,
    липсващи групи, OVC042///, европейски 4-цифрен VIS.
    """
    if not raw:
        return None

    # ICAO + час (ден от METAR хедъра ползваме само за верификация)
    hm = re.search(r"([A-Z]{4})\s+(\d{2})(\d{2})(\d{2})Z", raw)
    if not hm:
        return None
    icao = hm.group(1)
    # metar_day  = int(hm.group(2))  # не ни трябва — имаме реалната дата
    # metar_hour = int(hm.group(3))  # не ни трябва — имаме я от префикса
    # metar_min  = int(hm.group(4))

    # ── Вятър ──────────────────────────────────────────────────────
    wm = _RE_WIND.search(raw)
    wind_dir  = None
    wind_spd  = None
    wind_gust = None
    if wm:
        ds = wm.group(1)
        wind_dir  = None if ds == "VRB" else int(ds)
        wind_spd  = int(wm.group(2))
        wind_gust = int(wm.group(3)) if wm.group(3) else wind_spd

    # ── Видимост ───────────────────────────────────────────────────
    cavok = bool(re.search(r"\b(CAVOK|NSC|NCD)\b", raw))
    vis   = 9999 if cavok else None
    if not cavok:
        vm = _RE_VIS4.search(raw)
        if vm:
            vis = int(vm.group(1))   # 9999 или 4-цифрена стойност

    # ── Облачност ──────────────────────────────────────────────────
    clouds = []
    for cm in _RE_CLOUD.finditer(raw):
        cover     = cm.group(1)
        height_ft = int(cm.group(2)) * 100
        clouds.append((cover, height_ft))

    has_bkn_ovc_low = any(
        c in ("BKN", "OVC") and h < CEIL_LOW_FT
        for c, h in clouds
    )

    # ── Явления ────────────────────────────────────────────────────
    wx_tokens: set[str] = set()
    for tok in raw.split():
        tok = tok.lstrip("+-")
        if tok in _SIG_PRECIP | _LIGHT_DZ | {"FG", "BR", "HZ", "TS",
                                              "TSRA", "TSSN", "FZFG", "FZDZ", "FZRA"}:
            wx_tokens.add(tok)
        # Разпознаваме и комбинирани кодове
        for sp in _SIG_PRECIP:
            if tok == sp or tok == "-" + sp or tok == "+" + sp:
                wx_tokens.add(sp)

    has_fog       = ("FG" in wx_tokens) or (vis is not None and vis < FOG_VIS_M)
    has_sig_prec  = bool(wx_tokens & _SIG_PRECIP)
    has_dz        = "DZ" in wx_tokens

    # ── Температура / точка на оросяване ───────────────────────────
    temp = dewp = spread = None
    tm = _RE_TEMP.search(raw)
    if tm:
        try:
            temp  = _todeg(tm.group(1))
            dewp  = _todeg(tm.group(2))
            spread = round(temp - dewp, 1)
        except ValueError:
            pass

    return {
        "raw":             raw,
        "icao":            icao,
        "wind_spd":        wind_spd,
        "wind_gust":       wind_gust,
        "vis":             vis,
        "cavok":           cavok,
        "clouds":          clouds,
        "wx":              wx_tokens,
        "temp":            temp,
        "dewp":            dewp,
        "spread":          spread,
        "has_fog":         has_fog,
        "has_sig_prec":    has_sig_prec,
        "has_dz":          has_dz,
        "has_bkn_ovc_low": has_bkn_ovc_low,
    }


# ══════════════════════════════════════════════════════════════════
#  ЗАРЕЖДАНЕ И ГРУПИРАНЕ ПО НОЩИ
# ══════════════════════════════════════════════════════════════════

def load_airport_metars(icao: str) -> list[tuple[datetime, dict]]:
    """
    Зарежда всички METAR-и за даденото летище от конфигурирания файл.
    Връща списък от (datetime_utc, parsed_metar), хронологично сортиран.
    Прескача нечетими редове с предупреждение.
    """
    fpath = METAR_FILES.get(icao)
    if not fpath or not os.path.isfile(fpath):
        print(f"  ⚠  Файл не е намерен за {icao}: {fpath!r}")
        return []

    print(f"  ✓  Четем {icao} от {fpath}")
    result  = []
    skipped = 0

    with open(fpath, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line_date, raw = parse_line(line)
            if raw is None:
                continue
            if line_date is None:
                # Ред без реална дата — не можем да го групираме надеждно
                skipped += 1
                if skipped <= 3:
                    print(f"    ⚠  Ред {lineno}: без дата-префикс — пропускам")
                continue

            # Филтрираме само по нашето летище
            m = parse_metar(raw)
            if m is None:
                skipped += 1
                if skipped <= 3:
                    print(f"    ⚠  Ред {lineno}: неразпознат METAR — пропускам: {raw[:60]}")
                continue
            if m["icao"] != icao:
                continue    # чужд METAR в общ файл

            # Извличаме час от METAR хедъра (той е в UTC)
            hm = re.search(r"\d{6}Z", raw)
            if hm:
                h = int(hm.group(0)[2:4])
                mn = int(hm.group(0)[4:6])
            else:
                h, mn = 0, 0

            # Реконструираме пълен datetime_utc
            # Датата от префикса е датата на UTC наблюдението
            dt_utc = datetime(
                line_date.year, line_date.month, line_date.day,
                h, mn, 0, tzinfo=timezone.utc
            )
            m["dt_utc"] = dt_utc
            result.append((dt_utc, m))

    if skipped > 3:
        print(f"    ⚠  Общо пропуснати редове за {icao}: {skipped}")

    result.sort(key=lambda x: x[0])
    print(f"     → Заредени {len(result)} METAR-а за {icao}")
    return result


def group_by_night(records: list[tuple[datetime, dict]]) -> dict[date, list[dict]]:
    """
    Групира METAR-ите по нощи.
    Нощ на ден D = [D 16:00 UTC, D+1 09:00 UTC).
    Ключ: date(D) — датата на ден D.
    """
    nights: dict[date, list[dict]] = defaultdict(list)

    for dt_utc, m in records:
        h = dt_utc.hour
        d = dt_utc.date()

        if h >= NIGHT_START_HOUR:
            # 16:00–23:59 UTC → нощта принадлежи на ден D = d
            night_date = d
        elif h < NIGHT_END_HOUR:
            # 00:00–08:59 UTC → нощта е започнала предния ден D = d-1
            night_date = d - timedelta(days=1)
        else:
            # 09:00–15:59 UTC → дневен период, пропускаме
            continue

        nights[night_date].append(m)

    return nights


# ══════════════════════════════════════════════════════════════════
#  ПОМОЩНИ ФУНКЦИИ ЗА СТАТИСТИКА
# ══════════════════════════════════════════════════════════════════

def _wind_speeds(ms: list[dict]) -> list[float]:
    return [m["wind_spd"] for m in ms if m["wind_spd"] is not None]

def mean_wind(ms: list[dict]) -> float:
    sp = _wind_speeds(ms)
    return round(sum(sp) / len(sp), 1) if sp else 0.0

def max_wind(ms: list[dict]) -> float:
    sp = _wind_speeds(ms)
    return float(max(sp)) if sp else 0.0

def min_spread(ms: list[dict]) -> float | None:
    sp = [m["spread"] for m in ms if m["spread"] is not None]
    return round(min(sp), 1) if sp else None

def min_vis(ms: list[dict]) -> int | None:
    vs = [m["vis"] for m in ms if m["vis"] is not None]
    return min(vs) if vs else None

def fog_count(ms: list[dict]) -> int:
    return sum(1 for m in ms if m["has_fog"])

def fog_hours(ms: list[dict]) -> float:
    """Приблизителни часове мъгла (допускаме ~30 мин. между METAR-ите)."""
    return round(fog_count(ms) * 0.5, 1)

def bkn_ovc_frac(ms: list[dict]) -> float:
    if not ms:
        return 0.0
    return round(sum(1 for m in ms if m["has_bkn_ovc_low"]) / len(ms), 2)

def strong_wind_count(ms: list[dict]) -> int:
    return sum(1 for m in ms
               if m["wind_spd"] is not None and m["wind_spd"] >= WIND_STRONG_KT)


# ══════════════════════════════════════════════════════════════════
#  КЛАСИФИКАЦИЯ
# ══════════════════════════════════════════════════════════════════

def classify_night(ms: list[dict]) -> tuple[str, dict]:
    """
    Класифицира нощта. Приоритет: CFOG > CDRY > CLDY > DYNM.
    Връща (категория, метаданни-речник).
    """
    if not ms:
        return "UNCLASSIFIED", {}

    mw   = mean_wind(ms)
    mx   = max_wind(ms)
    mspr = min_spread(ms)
    mv   = min_vis(ms)
    fc   = fog_count(ms)
    fh   = fog_hours(ms)
    bkn  = bkn_ovc_frac(ms)
    swc  = strong_wind_count(ms)

    # Значим валеж (RA/SN/GR и т.н.) — DZ само извън мъглата блокира
    heavy_prec = any(m["has_sig_prec"] for m in ms)
    dz_out_fog = any(m["has_dz"] and not m["has_fog"] for m in ms)
    precip_blocks = heavy_prec or dz_out_fog

    # Ясност преди първия мъглив METAR
    fog_idx = [i for i, m in enumerate(ms) if m["has_fog"]]
    pre_fog = ms[:fog_idx[0]] if fog_idx else ms
    pf_clear = round(1.0 - bkn_ovc_frac(pre_fog), 2) if pre_fog else 1.0

    # Ясност за цялата нощ
    night_clear = round(1.0 - bkn, 2)

    # ── Условия ────────────────────────────────────────────────────
    wind_calm  = (mw <= WIND_MEAN_CALM and mx <= WIND_MAX_CALM)
    clear_pre  = (pf_clear  >= CLEAR_FRAC_MIN)
    clear_full = (night_clear >= CLEAR_FRAC_MIN)
    fog_ok     = (fc >= FOG_CNT_MIN)
    no_fog_vis = (mv is None or mv >= DRY_VIS_MIN_M) and fc == 0
    wet_ok     = (mspr is not None and mspr <= DRY_SPREAD_MAX)
    cloudy_ok  = (bkn >= CLOUDY_FRAC_MIN)
    dynm_ok    = (mw >= WIND_MEAN_DYNM or swc >= WIND_STRONG_CNT)

    meta = {
        "total_metars":    len(ms),
        "mean_wind_kt":    mw,
        "max_wind_kt":     mx,
        "strong_wind_cnt": swc,
        "min_spread_c":    mspr,
        "min_vis_m":       mv,
        "fog_cnt":         fc,
        "fog_hours":       fh,
        "bkn_ovc_frac":    bkn,
        "pre_fog_clear":   pf_clear,
        "night_clear":     night_clear,
        "sig_precip":      heavy_prec,
        "dz_out_fog":      dz_out_fog,
    }

    if wind_calm and clear_pre and not precip_blocks and fog_ok:
        return "CFOG", meta

    if wind_calm and clear_full and not heavy_prec and no_fog_vis and wet_ok:
        return "CDRY", meta

    if cloudy_ok:
        meta["cldy_had_precip"]      = heavy_prec
        meta["cldy_vis_below_2000"]  = (mv is not None and mv < DRY_VIS_MIN_M)
        return "CLDY", meta

    if dynm_ok:
        meta["dynm_had_fog"] = (fc > 0)
        return "DYNM", meta

    return "UNCLASSIFIED", meta


# ══════════════════════════════════════════════════════════════════
#  NEAR-MISS АНАЛИЗ
# ══════════════════════════════════════════════════════════════════

def near_miss(ms: list[dict], category: str, meta: dict) -> list[str]:
    """
    Проверява дали нощта е пропуснала CFOG или CDRY с точно едно условие.
    Връща списък с описания на пропуснатото условие.
    """
    if category in ("CFOG", "CDRY"):
        return []

    mw   = meta["mean_wind_kt"]
    mx   = meta["max_wind_kt"]
    mspr = meta["min_spread_c"]
    mv   = meta["min_vis_m"]
    fc   = meta["fog_cnt"]
    pfc  = meta["pre_fog_clear"]
    nfc  = meta["night_clear"]
    prec = meta["sig_precip"]
    bkn  = meta["bkn_ovc_frac"]

    wind_calm  = (mw <= WIND_MEAN_CALM and mx <= WIND_MAX_CALM)
    near_wind  = (mw <= NM_WIND_MEAN_MAX)
    clear_pre  = (pfc  >= CLEAR_FRAC_MIN)
    clear_full = (nfc  >= CLEAR_FRAC_MIN)
    fog_ok     = (fc   >= FOG_CNT_MIN)
    no_fog_vis = (mv is None or mv >= DRY_VIS_MIN_M) and fc == 0
    wet_ok     = (mspr is not None and mspr <= DRY_SPREAD_MAX)
    near_wet   = (mspr is not None and mspr <= NM_SPREAD_MAX)
    precip_ok  = not (meta["sig_precip"] or meta["dz_out_fog"])

    reasons = []

    # ── Почти CFOG ──
    cfog_fails = []
    if not wind_calm:
        cfog_fails.append(
            f"вятър (ср={mw} kt, макс={mx} kt; праг {WIND_MEAN_CALM}/{WIND_MAX_CALM} kt)"
        )
    if not clear_pre:
        cfog_fails.append(
            f"облачност преди мъглата ({pfc*100:.0f}% ясни; праг {CLEAR_FRAC_MIN*100:.0f}%)"
        )
    if not precip_ok:
        cfog_fails.append("значим валеж/DZ извън мъглата")
    if not fog_ok:
        cfog_fails.append(f"недостатъчно мъгливи METAR-и ({fc}; праг {FOG_CNT_MIN})")

    if len(cfog_fails) == 1 and near_wind:
        reasons.append(f"NEAR-CFOG: пропуснато → {cfog_fails[0]}")

    # ── Почти CDRY ──
    cdry_fails = []
    if not wind_calm:
        cdry_fails.append(f"вятър (ср={mw} kt, макс={mx} kt)")
    if not clear_full:
        cdry_fails.append(f"облачност ({bkn*100:.0f}% BKN/OVC; праг {CLEAR_FRAC_MIN*100:.0f}%)")
    if prec:
        cdry_fails.append("значим валеж")
    if not no_fog_vis:
        cdry_fails.append(f"VIS < {DRY_VIS_MIN_M} m или FG")
    if not wet_ok:
        cdry_fails.append(
            f"спред ({mspr} °C > {DRY_SPREAD_MAX} °C)"
        )

    if len(cdry_fails) == 1 and near_wet:
        reasons.append(f"NEAR-CDRY: пропуснато → {cdry_fails[0]}")

    return reasons


# ══════════════════════════════════════════════════════════════════
#  ЗАПИС НА ФАЙЛОВЕ
# ══════════════════════════════════════════════════════════════════

def _yn(val: bool) -> str:
    return "да" if val else "не"


def meta_header(icao: str, category: str, night_date: date, meta: dict) -> str:
    """Генерира заглавен блок с метаданни за case файла."""
    sep = "=" * 64
    lines = [
        sep,
        f"ЛЕТИЩЕ    : {icao}",
        f"КАТЕГОРИЯ : {category}",
        f"НОЩ       : {night_date} / {night_date + timedelta(days=1)} (UTC 16:00–09:00)",
        "-" * 64,
        f"Общо METAR-и в нощта  : {meta['total_metars']}",
        f"Среден вятър           : {meta['mean_wind_kt']} kt",
        f"Максимален вятър       : {meta['max_wind_kt']} kt",
    ]

    if meta.get("min_spread_c") is not None:
        lines.append(f"Мин. T−Td спред        : {meta['min_spread_c']} °C")
    if meta.get("min_vis_m") is not None:
        lines.append(f"Мин. видимост          : {meta['min_vis_m']} m")

    if category == "CFOG":
        lines += [
            f"METAR-и с мъгла        : {meta['fog_cnt']}",
            f"Прибл. часове мъгла    : {meta['fog_hours']} ч",
            f"Ясни METAR-и преди мъгла: {meta['pre_fog_clear']*100:.0f}%",
            f"Значим валеж           : {_yn(meta['sig_precip'])}",
        ]
    elif category == "CDRY":
        lines += [
            f"Ясни METAR-и (нощта)   : {meta['night_clear']*100:.0f}%",
            f"METAR-и с VIS < 2000 m : {_yn(not ((meta['min_vis_m'] or 9999) >= DRY_VIS_MIN_M))}",
        ]
    elif category == "CLDY":
        lines += [
            f"Дял BKN/OVC            : {meta['bkn_ovc_frac']*100:.0f}%",
            f"Валеж                  : {_yn(meta.get('cldy_had_precip', False))}",
            f"VIS < 2000 m           : {_yn(meta.get('cldy_vis_below_2000', False))}",
        ]
    elif category == "DYNM":
        lines += [
            f"METAR-и с ≥ {WIND_STRONG_KT} kt      : {meta['strong_wind_cnt']}",
            f"Мъгла въпреки вятъра   : {_yn(meta.get('dynm_had_fog', False))}",
        ]

    lines += [sep, "СУРОВИ METAR-И:", "-" * 64]
    return "\n".join(lines)


def write_case(icao: str, category: str, night_date: date,
               meta: dict, metars: list[dict], out_dir: str) -> str:
    """Записва case файл. Връща пътя."""
    fname = f"{icao}_{category}_{night_date}.txt"
    fpath = os.path.join(out_dir, fname)
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(meta_header(icao, category, night_date, meta))
        fh.write("\n")
        for m in metars:
            fh.write(m["raw"] + "\n")
    return fpath


# ══════════════════════════════════════════════════════════════════
#  ОБРАБОТКА НА ЛЕТИЩЕ
# ══════════════════════════════════════════════════════════════════

def process_airport(icao: str, out_dir: str) -> dict:
    """Обработва всички нощи за едно летище. Връща речник за SUMMARY."""
    print(f"\n── {icao} ──")
    records = load_airport_metars(icao)
    if not records:
        return {"icao": icao, "error": "Няма данни", "cases": [], "near_misses": []}

    nights = group_by_night(records)
    sorted_dates = sorted(nights.keys())
    print(f"  Обработвам {len(sorted_dates)} нощи "
          f"({sorted_dates[0] if sorted_dates else '—'} … "
          f"{sorted_dates[-1] if sorted_dates else '—'})")

    counts: dict[str, int] = defaultdict(int)
    cases        = []
    near_misses  = []

    for nd in sorted_dates:
        ms = sorted(nights[nd], key=lambda m: m["dt_utc"])
        if len(ms) < 2:
            continue    # прескачаме нощи с почти никакви данни

        category, meta = classify_night(ms)
        counts[category] += 1

        if category != "UNCLASSIFIED":
            fpath = write_case(icao, category, nd, meta, ms, out_dir)
            cases.append({
                "date":     nd,
                "category": category,
                "meta":     meta,
                "file":     os.path.basename(fpath),
            })

        for nm in near_miss(ms, category, meta):
            near_misses.append({"date": nd, "reason": nm, "meta": meta})

    print("  Категории: " +
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items()) if v))
    return {
        "icao":       icao,
        "error":      None,
        "counts":     dict(counts),
        "cases":      cases,
        "near_misses": near_misses,
    }


# ══════════════════════════════════════════════════════════════════
#  SUMMARY
# ══════════════════════════════════════════════════════════════════

def write_summary(results: list[dict], out_dir: str):
    """Генерира cases/SUMMARY.md."""
    cats = ["CFOG", "CDRY", "CLDY", "DYNM", "UNCLASSIFIED"]
    cols = ["Летище"] + cats + ["Общо"]
    sep  = "| " + " | ".join("---" for _ in cols) + " |"

    lines = [
        "# SUMMARY — Тестови метеорологични ситуации",
        "",
        "## Таблица: летище × категория",
        "",
        "| " + " | ".join(cols) + " |",
        sep,
    ]

    for r in results:
        if r.get("error"):
            empty = " | ".join("—" for _ in cats)
            lines.append(f"| {r['icao']} | {empty} | ⚠ {r['error']} |")
            continue
        total = sum(r.get("counts", {}).get(c, 0) for c in cats)
        row   = [r["icao"]] + [str(r.get("counts", {}).get(c, 0)) for c in cats] + [str(total)]
        lines.append("| " + " | ".join(row) + " |")

    lines += ["", "---", "", "## Пълен списък на ситуациите", ""]

    for r in results:
        lines.append(f"### {r['icao']}")
        if r.get("error"):
            lines.append(f"_{r['error']}_"); lines.append(""); continue
        if not r.get("cases"):
            lines.append("_Няма класифицирани случаи._"); lines.append(""); continue

        for c in r["cases"]:
            meta = c["meta"]
            summ = (
                f"мин.VIS={meta.get('min_vis_m','?')} m  "
                f"мъгла={meta.get('fog_hours',0)} ч  "
                f"ср.вятър={meta.get('mean_wind_kt','?')} kt  "
                f"мин.спред={meta.get('min_spread_c','?')} °C"
            )
            lines.append(
                f"- **{c['date']}** `{c['category']}` — {summ}"
                f" → `{c['file']}`"
            )
        lines.append("")

    lines += ["---", "", "## Near-miss: почти CFOG или CDRY", ""]
    any_nm = False
    for r in results:
        nms = r.get("near_misses", [])
        if not nms:
            continue
        any_nm = True
        lines.append(f"### {r['icao']}")
        for nm in nms:
            lines.append(f"- **{nm['date']}**: {nm['reason']}")
        lines.append("")

    if not any_nm:
        lines.append("_Няма near-miss случаи._")

    fpath = os.path.join(out_dir, "SUMMARY.md")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\n✓ Отчет записан: {fpath}")


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print("=" * 64)
    print("  CASE SELECTOR — 1D модел за мъгла / Български летища")
    print("=" * 64)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = []
    for icao in AIRPORTS:
        r = process_airport(icao, OUTPUT_DIR)
        results.append(r)

    write_summary(results, OUTPUT_DIR)

    # ── Конзолно обобщение ────────────────────────────────────────
    print("\n" + "=" * 64)
    print("ОБОБЩЕНИЕ:")
    total = 0
    for r in results:
        if r.get("error"):
            print(f"  {r['icao']}: ⚠ {r['error']}")
        else:
            n = len(r.get("cases", []))
            total += n
            cnts = "  ".join(
                f"{k}={v}" for k, v in sorted(r.get("counts", {}).items()) if v
            )
            print(f"  {r['icao']}: {n} случая  {cnts}")
    print(f"\n  Общо класифицирани случаи: {total}")
    print("=" * 64)


if __name__ == "__main__":
    main()
