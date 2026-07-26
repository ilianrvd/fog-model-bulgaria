"""
test_levels.py
==============
Проверява кои pressure levels са налични от historical-forecast-api
за LBSF и какви са стойностите им.
"""
import urllib.request, urllib.parse, json

ELEV = 531  # LBSF elevation

params = {
    "latitude": 42.697, "longitude": 23.406,
    "hourly": ",".join([
        "temperature_2m", "surface_pressure",
        "temperature_1000hPa", "temperature_975hPa", "temperature_950hPa",
        "temperature_925hPa", "temperature_900hPa", "temperature_875hPa",
        "temperature_850hPa", "temperature_800hPa", "temperature_750hPa",
        "temperature_700hPa",
        "geopotential_height_1000hPa", "geopotential_height_975hPa",
        "geopotential_height_950hPa", "geopotential_height_925hPa",
        "geopotential_height_900hPa", "geopotential_height_875hPa",
        "geopotential_height_850hPa",
        "relativehumidity_1000hPa", "relativehumidity_975hPa",
        "relativehumidity_950hPa", "relativehumidity_925hPa",
        "relativehumidity_900hPa", "relativehumidity_875hPa",
        "relativehumidity_850hPa",
        "windspeed_1000hPa", "winddirection_1000hPa",
        "windspeed_975hPa", "winddirection_975hPa",
        "windspeed_950hPa", "winddirection_950hPa",
        "windspeed_925hPa", "winddirection_925hPa",
    ]),
    "start_date": "2024-01-18", "end_date": "2024-01-19",
    "timezone": "UTC", "models": "icon_eu",
}

url = "https://historical-forecast-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
print(f"URL: {url[:80]}...")

req = urllib.request.Request(url, headers={"User-Agent": "fog-model/1.0"})
with urllib.request.urlopen(req, timeout=20) as r:
    data = json.loads(r.read())

hourly = data["hourly"]
times  = hourly["time"]
ti     = times.index("2024-01-18T18:00")

sfc_p = hourly["surface_pressure"][ti]
print(f"\nLBSF 18.01.2024 18UTC  sfc_p={sfc_p:.0f} hPa")
print(f"\n{'Ниво hPa':>10} {'z AMSL m':>10} {'z AGL m':>10} {'T °C':>8} {'RH %':>6} {'WS kt':>6}")
print("-"*55)

for lev in [1000, 975, 950, 925, 900, 875, 850, 800, 750, 700]:
    T_C = hourly.get(f"temperature_{lev}hPa", [None]*len(times))[ti]
    z_m = hourly.get(f"geopotential_height_{lev}hPa", [None]*len(times))[ti]
    rh  = hourly.get(f"relativehumidity_{lev}hPa", [None]*len(times))[ti]
    ws  = hourly.get(f"windspeed_{lev}hPa", [None]*len(times))[ti]

    if lev > sfc_p + 5:
        print(f"{lev:10d}  {'под земята':>30}")
        continue
    if T_C is None or z_m is None:
        print(f"{lev:10d}  {'ЛИПСВА':>30}")
        continue

    z_agl = max(float(z_m) - ELEV, 0)
    print(f"{lev:10d}  {float(z_m):10.0f}  "
          f"{z_agl:10.0f}  {float(T_C):8.1f}  "
          f"{float(rh) if rh else 0:6.0f}  "
          f"{float(ws)*0.539957 if ws else 0:6.1f}")

print(f"\nT_2m = {hourly['temperature_2m'][ti]}°C")
print(f"\nНалични нива над земята: ", end="")
available = []
for lev in [1000, 975, 950, 925, 900, 875, 850, 800, 750, 700]:
    T_C = hourly.get(f"temperature_{lev}hPa", [None]*len(times))[ti]
    if T_C is not None and lev <= sfc_p + 5:
        available.append(lev)
print(available)
EOF
