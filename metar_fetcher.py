"""
metar_fetcher.py
================
Изтегля актуален METAR от aviationweather.gov REST API.
Без акаунт, без API ключ — публичен достъп.

Документация: https://aviationweather.gov/data/api/
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

AWC_BASE = "https://aviationweather.gov/api/data/metar"


def fetch_metar(icao: str, hours: int = 2) -> str:
    """
    Изтегля последния METAR за летището.

    Параметри
    ----------
    icao  : ICAO код (LBSF, LBWN, LBBG, LBPD, LBGO)
    hours : колко часа назад да търси (default 2)

    Връща
    ------
    str — суров METAR низ
    """
    params = {
        "ids"    : icao,
        "format" : "json",
        "hours"  : hours,
    }
    url = AWC_BASE + "?" + urllib.parse.urlencode(params)

    print(f"[METAR] Изтегляне за {icao}...")
    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0 (aviation-met@bulatsa.bg)",
        "Accept"    : "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"AWC HTTP грешка: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Мрежова грешка: {e.reason}")

    if not data:
        raise RuntimeError(f"Няма METAR данни за {icao} в последните {hours} часа.")

    # Вземаме най-новия
    latest = data[0]
    raw    = latest.get("rawOb") or latest.get("raw_text") or ""

    if not raw:
        raise RuntimeError(f"Празен METAR за {icao}.")

    obs_time = latest.get("obsTime") or latest.get("reportTime", "")
    print(f"[METAR] {icao} @ {obs_time}: {raw}")
    return raw


def fetch_all_airports(icao_list: list, hours: int = 2) -> dict:
    """
    Изтегля METAR за всички летища наведнъж (една заявка).

    Параметри
    ----------
    icao_list : списък с ICAO кодове
    hours     : часове назад

    Връща
    ------
    dict {icao: raw_metar_str}
    """
    ids_str = ",".join(icao_list)
    params  = {"ids": ids_str, "format": "json", "hours": hours}
    url     = AWC_BASE + "?" + urllib.parse.urlencode(params)

    print(f"[METAR] Изтегляне за {ids_str}...")
    req = urllib.request.Request(url, headers={
        "User-Agent": "fog-model-dprvd/1.0",
        "Accept"    : "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"METAR заявка неуспешна: {e}")

    result = {}
    for rec in data:
        icao = rec.get("icaoId") or rec.get("station_id", "")
        raw  = rec.get("rawOb")  or rec.get("raw_text", "")
        if icao and raw and icao not in result:   # само последния
            result[icao] = raw
            print(f"[METAR] {icao}: {raw}")

    # Ако някое летище липсва — предупреждение
    for icao in icao_list:
        if icao not in result:
            print(f"[METAR] ⚠ Няма данни за {icao}")

    return result


if __name__ == "__main__":
    import sys
    airports = sys.argv[1:] if len(sys.argv) > 1 else ["LBSF", "LBWN", "LBBG", "LBPD", "LBGO"]
    metars = fetch_all_airports(airports)
    print(f"\nРезултат: {len(metars)} METAR")
    for k, v in metars.items():
        print(f"  {k}: {v}")
