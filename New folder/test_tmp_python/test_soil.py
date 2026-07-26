import urllib.request, json

print("Търсим наземна ICON клетка около Варна (43.232N):")
print(f"{'lon':>6}  {'T_soil':>8}  {'T_2m':>6}")
print("-" * 28)

for lon in [27.3, 27.4, 27.5, 27.6, 27.65, 27.7, 27.75, 27.8, 27.825, 27.9]:
    url = (f"https://historical-forecast-api.open-meteo.com/v1/forecast"
           f"?latitude=43.232&longitude={lon}"
           f"&hourly=soil_temperature_0cm,temperature_2m"
           f"&start_date=2024-11-05&end_date=2024-11-05"
           f"&timezone=UTC&models=icon_eu")
    d = json.loads(urllib.request.urlopen(url).read())["hourly"]
    ts = d["soil_temperature_0cm"][18]
    t2 = d["temperature_2m"][18]
    note = " ← ТЕКУЩА" if lon == 27.825 else (" ← морска?" if ts < 5 else " ← наземна!")
    print(f"{lon:>6.3f}  {ts:>8.1f}  {t2:>6.1f}{note}")
