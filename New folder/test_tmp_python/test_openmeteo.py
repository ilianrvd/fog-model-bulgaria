"""
test_openmeteo.py
=================
Тества различни Open-Meteo endpoints за оперативни ICON-EU данни.
Пусни от лаптопа: python test_openmeteo.py
"""

import urllib.request, urllib.parse, json, time
from datetime import datetime, timezone, timedelta

LAT, LON = 42.697, 23.406  # LBSF
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
TOMORROW = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")

params_hourly = "temperature_2m,dewpoint_2m,windspeed_10m,relativehumidity_1000hPa,temperature_1000hPa,geopotential_height_1000hPa"

tests = [
    ("1. api.open-meteo.com (оперативен)",
     f"https://api.open-meteo.com/v1/dwd-icon?latitude={LAT}&longitude={LON}&hourly={params_hourly}&forecast_days=1&timezone=UTC&models=icon_eu"),

    ("2. historical-forecast-api (вчера)",
     f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&hourly={params_hourly}&start_date={TODAY}&end_date={TOMORROW}&timezone=UTC&models=icon_eu"),

    ("3. ensemble-api (оперативен ICON-EU ensemble)",
     f"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={LAT}&longitude={LON}&hourly=temperature_2m,windspeed_10m&models=icon_eu&forecast_days=1&timezone=UTC"),

    ("4. DWD opendata (директно)",
     "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/t/"),
]

print(f"Тест от: {NOW.strftime('%Y-%m-%d %H:%M UTC')}\n")

for name, url in tests:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fog-model/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(300)
        elapsed = time.time() - t0

        # Проверяваме дали данните са за ДНЕС
        body_str = body.decode("utf-8", errors="replace")
        has_today = TODAY in body_str
        has_data  = len(body) > 50

        print(f"✓ {name}")
        print(f"  Отговор: {elapsed:.1f}s  Данни за днес: {'ДА' if has_today else 'НЕ'}")
        print(f"  Извадка: {body_str[:120]}")

        # Ако е JSON — показваме T_2m за текущия час
        try:
            data = json.loads(body_str)
            times = data.get("hourly", {}).get("time", [])
            temps = data.get("hourly", {}).get("temperature_2m", [])
            if times and temps:
                now_str = NOW.strftime("%Y-%m-%dT%H:00")
                if now_str in times:
                    idx = times.index(now_str)
                    print(f"  T_2m @ {now_str} UTC = {temps[idx]}°C  ✓ ОПЕРАТИВНО")
                else:
                    print(f"  Текущият час {now_str} НЕ е в данните")
        except:
            pass

    except Exception as e:
        elapsed = time.time() - t0
        print(f"✗ {name}")
        print(f"  Грешка: {e} ({elapsed:.1f}s)")
    print()
