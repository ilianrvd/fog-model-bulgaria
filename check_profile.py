import run_case
p = run_case.fetch_icon_historical('LBWN', '2024-11-05', 18, 13)
print('z[0]=%.0f  T[0]=%.1f  T_soil=%.1f' % (p['z'][0], p['T'][0]-273.15, p['T_soil']-273.15))