"""
output.py
=========
Изходни функции за 1D модела за мъгла:
  - CSV таблица с прогнозата
  - ASCII диаграма на видимостта (терминал)
  - Matplotlib визуализация (ако е инсталиран)
"""

import numpy as np
import csv
import os


# ──────────────────────────────────────────────────────────────────────────────
# CSV изход
# ──────────────────────────────────────────────────────────────────────────────

def save_surface_csv(history: list, out_path: str = "fog_forecast_sfc.csv"):
    """
    Записва приземна прогноза (всеки час) в CSV.

    Колони: time_h, hour_utc, T_sfc_C, RH_sfc_pct, LWC_sfc_gm3,
            VIS_sfc_m, CAT
    """
    fieldnames = [
        'time_h', 'hour_utc', 'T_sfc_C', 'RH_sfc_pct',
        'LWC_sfc_gm3', 'VIS_sfc_m', 'CAT'
    ]
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in history:
            w.writerow({
                'time_h'      : r['time_h'],
                'hour_utc'    : r['hour_utc'],
                'T_sfc_C'     : round(r['T_sfc'] - 273.15, 2),
                'RH_sfc_pct'  : round(r['rh_sfc'] * 100, 1),
                'LWC_sfc_gm3' : round(r['ql_sfc'] * 1000, 4),
                'VIS_sfc_m'   : int(r['vis_sfc']),
                'CAT'         : r['cat'],
            })
    print(f"[CSV] Записан: {out_path}")
    return out_path


def save_profile_csv(history: list, out_path: str = "fog_forecast_profiles.csv"):
    """
    Записва вертикалните профили за всеки час в CSV.
    """
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['time_h', 'hour_utc', 'z_m',
                    'T_C', 'RH_pct', 'LWC_gm3', 'VIS_m'])
        for r in history:
            for k in range(len(r['z'])):
                w.writerow([
                    r['time_h'], r['hour_utc'],
                    round(float(r['z'][k]), 1),
                    round(float(r['T'][k]) - 273.15, 2),
                    round(float(r['rh'][k]) * 100, 1),
                    round(float(r['ql'][k]) * 1000, 5),
                    int(float(r['vis'][k])),
                ])
    print(f"[CSV] Записан: {out_path}")
    return out_path


# ──────────────────────────────────────────────────────────────────────────────
# ASCII диаграма (терминал)
# ──────────────────────────────────────────────────────────────────────────────

def print_visibility_timeline(history: list):
    """
    Принтира времева диаграма на видимостта в ASCII.
    █ < 200 m   ▓ 200–600 m   ░ 600–1000 m   · > 1000 m
    """
    print("\n" + "═" * 65)
    print("  ПРОГНОЗА ВИДИМОСТ  (ASCII timeline — LBSF)")
    print("  █<200m  ▓200-600m  ░600-1000m  ·>1000m")
    print("═" * 65)

    def vis_char(v):
        if v < 200:  return '█'
        if v < 600:  return '▓'
        if v < 1000: return '░'
        return '·'

    print(f"  {'UTC':>5}  {'VIS':>6}  {'CAT':>5}  Диаграма")
    print("  " + "─" * 60)
    for r in history:
        bar_len = max(1, int((10000 - r['vis_sfc']) / 200))
        bar     = vis_char(r['vis_sfc']) * min(bar_len, 40)
        print(f"  {r['hour_utc']:5.1f}  {r['vis_sfc']:6.0f}  "
              f"{r['cat']:>5}  {bar}")
    print("═" * 65)


# ──────────────────────────────────────────────────────────────────────────────
# Matplotlib визуализация
# ──────────────────────────────────────────────────────────────────────────────

def plot_forecast(history: list, out_png: str = "fog_forecast.png",
                  show: bool = False):
    """
    4-панелна графика:
      1. Видимост + категория
      2. Приземна T и Td
      3. Приземна RH
      4. Хоризонтален LWC профил (hovmöller: z vs t)
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # без GUI
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        from matplotlib.patches import Patch
    except ImportError:
        print("[ВИЗУАЛИЗАЦИЯ] matplotlib не е инсталиран. Пропускам.")
        return None

    times = [r['hour_utc'] for r in history]
    vis   = [r['vis_sfc']  for r in history]
    T_sfc = [r['T_sfc'] - 273.15 for r in history]
    rh    = [r['rh_sfc'] * 100   for r in history]
    ql    = [r['ql_sfc'] * 1000  for r in history]

    # Hovmöller матрица (z × time)
    nz = len(history[0]['z'])
    nt = len(history)
    lwc_mat = np.zeros((nz, nt))
    rh_mat  = np.zeros((nz, nt))
    for ti, r in enumerate(history):
        lwc_mat[:, ti] = r['ql'] * 1000
        rh_mat[:, ti]  = r['rh'] * 100
    z_arr = history[0]['z']

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), facecolor='#0d1b2a')
    for ax in axes.flat:
        ax.set_facecolor('#0d1b2a')
        ax.tick_params(colors='#b0c4de', labelsize=9)
        ax.spines[:].set_color('#2a4a6a')
        ax.title.set_color('#7eb8e0')
        ax.xaxis.label.set_color('#b0c4de')
        ax.yaxis.label.set_color('#b0c4de')

    # ── Панел 1: Видимост ──
    ax1 = axes[0, 0]
    cat_colors = {'LIFR': '#ff4444', 'IFR': '#ff8800',
                  'MVFR': '#ffff00', 'VFR': '#44dd44'}
    ax1.plot(times, vis, color='#7eb8e0', linewidth=2.0, zorder=5)
    ax1.fill_between(times, 0, vis, alpha=0.25, color='#7eb8e0')
    # Цветни ленти за категориите
    for cat, col in cat_colors.items():
        thresh = {'LIFR': (0,200), 'IFR': (200,600),
                  'MVFR': (600,800), 'VFR': (800,10000)}[cat]
        ax1.axhspan(thresh[0], thresh[1], alpha=0.08, color=col)
    ax1.axhline(800, color='#ffff00', lw=0.7, ls='--', alpha=0.5)
    ax1.axhline(600, color='#ff8800', lw=0.7, ls='--', alpha=0.5)
    ax1.axhline(200, color='#ff4444', lw=0.7, ls='--', alpha=0.5)
    ax1.set_ylim(0, 10500)
    ax1.set_xlabel('UTC час')
    ax1.set_ylabel('Видимост [m]')
    ax1.set_title('Прогноза видимост — LBSF')
    ax1.grid(True, alpha=0.15, color='#7eb8e0')

    # ── Панел 2: T и точка на росата ──
    ax2 = axes[0, 1]
    ax2.plot(times, T_sfc, color='#ff8866', lw=2, label='T (°C)')
    # Оценяваме Td от RH и T
    Td_est = [t - (100 - r) / 5.0 for t, r in zip(T_sfc, rh)]
    ax2.plot(times, Td_est, color='#66bbff', lw=2, ls='--', label='Td (°C)')
    ax2.set_xlabel('UTC час')
    ax2.set_ylabel('Температура [°C]')
    ax2.set_title('T и Td приземно')
    ax2.legend(facecolor='#0d1b2a', labelcolor='white', fontsize=8)
    ax2.grid(True, alpha=0.15, color='#7eb8e0')

    # ── Панел 3: RH ──
    ax3 = axes[1, 0]
    ax3.plot(times, rh, color='#44ddaa', lw=2)
    ax3.axhline(100, color='#ffffff', lw=0.7, ls=':', alpha=0.4)
    ax3.set_ylim(50, 105)
    ax3.set_xlabel('UTC час')
    ax3.set_ylabel('RH [%]')
    ax3.set_title('Относителна влажност приземно')
    ax3.fill_between(times, 98, np.clip(rh, 0, 105),
                     where=[r >= 98 for r in rh],
                     alpha=0.3, color='#44ddaa', label='≥ 98%')
    ax3.legend(facecolor='#0d1b2a', labelcolor='white', fontsize=8)
    ax3.grid(True, alpha=0.15, color='#7eb8e0')

    # ── Панел 4: Hovmöller LWC ──
    ax4 = axes[1, 1]
    tt_arr = np.array(times)
    zz_arr = z_arr
    pcm = ax4.pcolormesh(tt_arr, zz_arr, lwc_mat,
                          cmap='Blues', shading='auto',
                          vmin=0, vmax=0.3)
    cbar = fig.colorbar(pcm, ax=ax4, label='LWC [g/m³]')
    cbar.ax.yaxis.label.set_color('#b0c4de')
    cbar.ax.tick_params(colors='#b0c4de')
    ax4.set_xlabel('UTC час')
    ax4.set_ylabel('Височина AGL [m]')
    ax4.set_title('LWC профил (Hovmöller)')

    plt.suptitle('ДП РВД — Едномерен модел за мъгла (PAFOG-type) LBSF',
                 color='#7eb8e0', fontsize=13, fontweight='bold', y=1.01)
    plt.tight_layout()
    plt.savefig(out_png, dpi=150, bbox_inches='tight',
                facecolor='#0d1b2a', edgecolor='none')
    if show:
        plt.show()
    plt.close()
    print(f"[ВИЗУАЛИЗАЦИЯ] Записана: {out_png}")
    return out_png


# ──────────────────────────────────────────────────────────────────────────────
# Текстов доклад (NOTAM-стил)
# ──────────────────────────────────────────────────────────────────────────────

def print_summary_report(history: list, station: str = "LBSF"):
    """Принтира кратък прогнозен доклад в текстов вид."""
    print("\n" + "=" * 65)
    print(f"  ПРОГНОЗЕН ДОКЛАД — {station}")
    print(f"  Период: +{history[0]['time_h']:.0f}h – +{history[-1]['time_h']:.0f}h")
    print("=" * 65)

    fog_periods   = []
    in_fog        = False
    fog_start     = None
    min_vis       = 10000
    min_vis_time  = None

    for r in history:
        v = r['vis_sfc']
        if v < 1000 and not in_fog:
            in_fog = True; fog_start = r['hour_utc']
        elif v >= 1000 and in_fog:
            in_fog = False
            fog_periods.append((fog_start, r['hour_utc']))
        if v < min_vis:
            min_vis = v; min_vis_time = r['hour_utc']

    if in_fog:
        fog_periods.append((fog_start, history[-1]['hour_utc']))

    if fog_periods:
        print("\n  ПРОГНОЗИРАНИ ПЕРИОДИ С НАМАЛЕНА ВИДИМОСТ (<1000m):")
        for s, e in fog_periods:
            print(f"    {s:05.1f} UTC — {e:05.1f} UTC")
        print(f"\n  МИНИМАЛНА ВИДИМОСТ: {min_vis:.0f} m @ {min_vis_time:.1f} UTC")
    else:
        print("\n  Не се прогнозира мъгла/намалена видимост.")

    print("\n  КАТЕГОРИЯ ПО ЧАСОВЕ:")
    for r in history:
        bar_vis = "█" * max(0, int((1 - min(r['vis_sfc'], 1000) / 1000) * 20))
        print(f"  {r['hour_utc']:5.1f}UTC  {r['cat']:>5}  {r['vis_sfc']:5.0f}m  {bar_vis}")
    print("=" * 65)
