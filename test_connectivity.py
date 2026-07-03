"""Тест на мрежова свързаност от GitHub Actions."""
import urllib.request
import time

urls = [
    ("Open-Meteo",        "https://api.open-meteo.com/v1/forecast?latitude=42.7&longitude=23.4&hourly=temperature_2m&forecast_days=1"),
    ("Open-Meteo DWD",    "https://api.open-meteo.com/v1/dwd-icon?latitude=42.7&longitude=23.4&hourly=temperature_2m&forecast_days=1"),
    ("Historical",        "https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=42.7&longitude=23.4&hourly=temperature_2m&start_date=2026-07-01&end_date=2026-07-02"),
    ("DWD opendata",      "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/t/"),
    ("OGIMET",            "https://www.ogimet.com/cgi-bin/getmetar?icao=LBSF&begin=202607030000&end=202607030600&lang=en"),
    ("aviationweather",   "https://aviationweather.gov/api/data/metar?ids=LBSF&format=json&hours=2"),
]

for name, url in urls:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fog-model/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(100)
        print(f"✓ {name}: OK ({time.time()-t0:.1f}s)  {body[:50]}")
    except Exception as e:
        print(f"✗ {name}: {e} ({time.time()-t0:.1f}s)")
