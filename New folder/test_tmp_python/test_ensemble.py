"""Тест на ensemble-api структура."""
import urllib.request, urllib.parse, json
from datetime import datetime, timezone

url = ("https://ensemble-api.open-meteo.com/v1/ensemble?"
       "latitude=42.697&longitude=23.406"
       "&hourly=temperature_2m,temperature_850hPa,windspeed_10m,relativehumidity_1000hPa"
       "&models=icon_eu&forecast_days=2&timezone=UTC")

req = urllib.request.Request(url, headers={"User-Agent": "fog-model/1.0"})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read())

hourly = data["hourly"]
now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
times   = hourly["time"]

print(f"Полета: {list(hourly.keys())}")
print(f"Часове: {len(times)}  първи: {times[0]}  последен: {times[-1]}")
print(f"Текущ час {now_str} в данните: {now_str in times}")

# Структура на T_2m
t2m = hourly["temperature_2m"]
print(f"\nТип temperature_2m: {type(t2m)}")
if isinstance(t2m, list):
    print(f"  Дължина: {len(t2m)}")
    print(f"  Първи елемент тип: {type(t2m[0])}")
    if isinstance(t2m[0], list):
        print(f"  → 2D масив (ensemble members × часове)")
        print(f"  Members: {len(t2m)}  Hours: {len(t2m[0])}")
        if now_str in times:
            idx = times.index(now_str)
            print(f"  T_2m @ {now_str}: member0={t2m[0][idx]}°C")
    else:
        print(f"  → 1D масив (часове)")
        if now_str in times:
            idx = times.index(now_str)
            print(f"  T_2m @ {now_str} UTC = {t2m[idx]}°C")
