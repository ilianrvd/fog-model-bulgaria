import urllib.request, json

print("2D скан за наземна ICON клетка около Варна:")
print(f"{'lat':>6} {'lon':>6}  {'T_soil':>8}  {'T_2m':>6}")
print("-" * 36)

best = None
for lat in [43.0, 43.1, 43.2, 43.3, 43.4, 43.5]:
    for lon in [27.3, 27.4, 27.5, 27.6, 27.7, 27.8, 27.9, 28.0, 28.1]:
        url = (f"https://historical-forecast-api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               f"&hourly=soil_temperature_0cm,temperature_2m"
               f"&start_date=2024-11-05&end_date=2024-11-05"
               f"&timezone=UTC&models=icon_eu")
        try:
            d = json.loads(urllib.request.urlopen(url).read())["hourly"]
            ts = d["soil_temperature_0cm"][18]
            t2 = d["temperature_2m"][18]
            note = " ← НАЗЕМНА!" if ts > 6 else ""
            if ts > 6:
                best = (lat, lon, ts, t2)
            if note or abs(lat-43.2)<0.05:
                print(f"{lat:>6.1f} {lon:>6.1f}  {ts:>8.1f}  {t2:>6.1f}{note}")
        except Exception as e:
            print(f"{lat:>6.1f} {lon:>6.1f}  ГРЕШКА: {e}")

print()
if best:
    print(f"Най-добра наземна точка: lat={best[0]} lon={best[1]} T_soil={best[2]:.1f} T_2m={best[3]:.1f}")
else:
    print("Не е намерена наземна точка — T_soil корекцията е единственият изход.")
