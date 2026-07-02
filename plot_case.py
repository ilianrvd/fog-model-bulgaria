"""
plot_case.py
============
Чете CSV от case_output и прави чиста графика:
  - Видимост [m] с ICAO категории
  - Температура T и Td [°C]
  - Обща времева ос: реални datetime стойности

Употреба
--------
python plot_case.py case_output/LBSF_2026-07-01_18UTC_forecast.csv
python plot_case.py case_output/LBSF_2026-07-01_18UTC_forecast.csv \
                   case_output/LBGO_2026-07-01_18UTC_forecast.csv
"""

import sys, os, csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta

def load_csv(path):
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            rows.append({
                'time_h'    : float(row['time_h']),
                'hour_utc'  : float(row['hour_utc']),
                'T_C'       : float(row['T_sfc_C']),
                'RH'        : float(row['RH_sfc_pct']),
                'VIS'       : float(row['VIS_sfc_m']),
                'CAT'       : row['CAT'],
            })
    # Td от T и RH (Magnus)
    for r in rows:
        T = r['T_C']
        RH = max(r['RH'], 1)
        gamma = np.log(RH/100) + 17.67*T/(243.5+T)
        r['Td_C'] = 243.5 * gamma / (17.67 - gamma)
    return rows

def make_datetimes(rows, date_str, hour0):
    """Построява непрекъсната datetime ос от time_h."""
    base = datetime.strptime(f"{date_str}T{hour0:02d}:00", "%Y-%m-%dT%H:%M")
    return [base + timedelta(hours=r['time_h']) for r in rows]

def plot_forecast_clean(csv_paths, out_png="forecast_clean.png"):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7),
                                    facecolor='#0d1b2a', sharex=True)
    fig.subplots_adjust(hspace=0.08)

    colors_vis = ['#4fc3f7', '#ff8c42', '#e84855', '#2ecc71']
    line_colors = ['#4fc3f7', '#f9a825', '#ef5350', '#66bb6a', '#ab47bc']

    for ax in (ax1, ax2):
        ax.set_facecolor('#0d1b2a')
        ax.tick_params(colors='#b0c4de', labelsize=9)
        for spine in ax.spines.values():
            spine.set_color('#2a4a6a')
        ax.yaxis.label.set_color('#b0c4de')
        ax.xaxis.label.set_color('#b0c4de')
        ax.title.set_color('#7eb8e0')
        ax.grid(True, alpha=0.15, color='#7eb8e0', linewidth=0.5)

    # ── Парсираме имената на файловете за дата и час ──
    all_data = []
    for path in csv_paths:
        rows = load_csv(path)
        # Извличаме дата и час от filename: LBSF_2026-07-01_18UTC_forecast.csv
        base = os.path.basename(path)
        parts = base.split('_')
        icao     = parts[0]
        date_str = parts[1]
        hour0    = int(parts[2].replace('UTC',''))
        dts = make_datetimes(rows, date_str, hour0)
        all_data.append((icao, rows, dts))

    # ── Панел 1: Видимост ──
    ax1.set_title("Прогноза видимост и температура", fontsize=12, pad=8)

    # Категорийни ленти
    cat_bands = [
        (0,   200,  '#ff000018', 'LIFR'),
        (200, 600,  '#ff880018', 'IFR'),
        (600, 800,  '#ffff0018', 'MVFR'),
    ]
    for y0, y1, col, label in cat_bands:
        ax1.axhspan(y0, y1, color=col, zorder=0)
    ax1.axhline(800, color='#ffff00', lw=0.6, ls='--', alpha=0.5)
    ax1.axhline(600, color='#ff8800', lw=0.6, ls='--', alpha=0.5)
    ax1.axhline(200, color='#ff4444', lw=0.6, ls='--', alpha=0.5)

    for i, (icao, rows, dts) in enumerate(all_data):
        vis = [r['VIS'] for r in rows]
        col = line_colors[i % len(line_colors)]
        ax1.plot(dts, vis, color=col, lw=2, label=icao, zorder=5)
        ax1.fill_between(dts, 0, vis, alpha=0.12, color=col)

    ax1.set_ylim(0, 10500)
    ax1.set_ylabel('Видимост [m]')
    ax1.legend(facecolor='#0d1b2a', labelcolor='white', fontsize=9,
               loc='upper right', framealpha=0.7)

    # Анотации за категории вдясно
    ax1.text(1.002, 100/10500,  'LIFR', transform=ax1.transAxes,
             color='#ff6666', fontsize=7, va='center')
    ax1.text(1.002, 400/10500,  'IFR',  transform=ax1.transAxes,
             color='#ffaa66', fontsize=7, va='center')
    ax1.text(1.002, 700/10500,  'MVFR', transform=ax1.transAxes,
             color='#ffff88', fontsize=7, va='center')

    # ── Панел 2: Температура ──
    for i, (icao, rows, dts) in enumerate(all_data):
        T   = [r['T_C']  for r in rows]
        Td  = [r['Td_C'] for r in rows]
        col = line_colors[i % len(line_colors)]
        lbl = icao
        ax2.plot(dts, T,  color=col, lw=2,   label=f'{lbl} T',  zorder=5)
        ax2.plot(dts, Td, color=col, lw=1.5, label=f'{lbl} Td',
                 ls='--', alpha=0.75, zorder=4)

    ax2.set_ylabel('Температура [°C]')
    ax2.legend(facecolor='#0d1b2a', labelcolor='white', fontsize=9,
               loc='upper right', framealpha=0.7, ncol=2)

    # ── Времева ос ──
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m\n%H:%M'))
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax2.set_xlabel('Дата / час UTC')
    plt.setp(ax2.xaxis.get_majorticklabels(), color='#b0c4de', fontsize=8)

    # ── Заглавие ──
    if all_data:
        stations = " + ".join(d[0] for d in all_data)
        _, rows0, dts0 = all_data[0]
        period = f"{dts0[0].strftime('%d.%m.%Y %H:%M')} – {dts0[-1].strftime('%d.%m.%Y %H:%M')} UTC"
        fig.suptitle(f"ДП РВД — 1D Модел мъгла  |  {stations}  |  {period}",
                     color='#7eb8e0', fontsize=11, fontweight='bold', y=0.98)

    plt.savefig(out_png, dpi=150, bbox_inches='tight',
                facecolor='#0d1b2a', edgecolor='none')
    plt.close()
    print(f"Записана: {out_png}")
    return out_png

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Употреба: python plot_case.py <csv1> [csv2] [--out output.png]")
        sys.exit(1)

    csv_files = []
    out_png   = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--out' and i+1 < len(sys.argv):
            out_png = sys.argv[i+1]; i += 2
        else:
            csv_files.append(sys.argv[i]); i += 1

    if not out_png:
        base = os.path.splitext(csv_files[0])[0]
        out_png = base.replace('_forecast', '') + '_plot.png'

    plot_forecast_clean(csv_files, out_png)
