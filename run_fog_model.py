"""
run_fog_model.py
================
Главен скрипт за стартиране на 1D модела за мъгла (LBSF).

Употреба
--------
# С реални данни:
python run_fog_model.py --wrf wrfout_d01_2024-11-15_18:00:00 \
                        --metar "METAR LBSF 151820Z 09003KT 0200 FG VV001 03/03 Q1018="

# Само с WRF (без METAR корекция):
python run_fog_model.py --wrf wrfout_d01_2024-11-15_18:00:00

# С синтетичен профил (тест):
python run_fog_model.py --test

# Пълни опции:
python run_fog_model.py --help
"""

import argparse
import sys
import os
import numpy as np

# ── Локален import ──
sys.path.insert(0, os.path.dirname(__file__))
from fog_model    import FogModel1D
from wrf_reader   import read_wrf_profile, synthetic_profile
from metar_parser import parse_metar, apply_metar_correction, decode_and_print
from output       import (save_surface_csv, save_profile_csv,
                           print_visibility_timeline, plot_forecast,
                           print_summary_report)


# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="1D Fog Model (PAFOG-type) за LBSF — ДП РВД"
    )
    parser.add_argument('--wrf',    type=str,   default=None,
                        help='Път до WRF wrfout NetCDF файл')
    parser.add_argument('--tidx',   type=int,   default=0,
                        help='Индекс на времето в WRF файла (default=0)')
    parser.add_argument('--metar',  type=str,   default=None,
                        help='METAR низ в кавички')
    parser.add_argument('--test',   action='store_true',
                        help='Използва синтетичен профил (тест без данни)')
    parser.add_argument('--hours',  type=float, default=12.0,
                        help='Продължителност на прогнозата [h] (default=12)')
    parser.add_argument('--dt',     type=float, default=60.0,
                        help='Времева стъпка [s] (default=60)')
    parser.add_argument('--out',    type=str,   default='.',
                        help='Директория за изходни файлове')
    parser.add_argument('--no-plot', action='store_true',
                        help='Пропуска matplotlib графиката')
    parser.add_argument('--sfc-T',  type=float, default=4.0,
                        help='Приземна T за синтетичен профил [°C]')
    parser.add_argument('--sfc-rh', type=float, default=0.92,
                        help='Приземна RH за синтетичен профил [0..1]')
    parser.add_argument('--hour0',  type=float, default=20.0,
                        help='Начален UTC час за синтетичен профил')

    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print("\n" + "═" * 65)
    print("  ДП РВД — 1D МОДЕЛ ЗА МЪГЛА И ВИДИМОСТ (PAFOG-TYPE)")
    print("  Летище София LBSF")
    print("═" * 65)

    # ── 1. Зареждане на профила ──
    if args.test or args.wrf is None:
        print("\n[РЕЖ. ТЕСТ] Синтетичен WRF профил")
        profile = synthetic_profile(
            hour0=args.hour0,
            sfc_T_C=args.sfc_T,
            sfc_rh=args.sfc_rh
        )
    else:
        print(f"\n[WRF] Зареждане: {args.wrf}")
        profile = read_wrf_profile(args.wrf, target_time_idx=args.tidx)

    # ── 2. METAR корекция ──
    metar_dict = None
    if args.metar:
        print(f"\n[METAR] Декодиране...")
        metar_dict = decode_and_print(args.metar)
        profile    = apply_metar_correction(profile, metar_dict)
    else:
        print("\n[METAR] Не е подаден — без корекция на приземния слой.")
        profile['ql_init'] = np.zeros_like(profile['T'])

    # ── 3. Инициализация на модела ──
    print(f"\n[МОДЕЛ] Инициализация: {len(profile['z'])} нива, "
          f"z=[{profile['z'][0]:.0f}–{profile['z'][-1]:.0f}] m AGL")

    model = FogModel1D(
        z     = profile['z'],
        T     = profile['T'],
        qv    = profile['qv'],
        p     = profile['p'],
        u     = profile['u'],
        v     = profile['v'],
        hour0 = profile['hour0'],
        dt    = args.dt,
    )
    # Начална мъгла ако е наблюдавана в METAR
    model.ql = profile['ql_init'].copy()

    # ── 4. Прогноза ──
    print(f"\n[ПРОГНОЗА] Продължителност: {args.hours:.0f} h  |  dt={args.dt:.0f} s")
    print(f"           Начален час: {profile['hour0']:.1f} UTC\n")
    history = model.run(hours=args.hours, output_interval_min=60, verbose=True)

    # ── 5. Изход ──
    print("\n[ИЗХОД] Генериране на файлове...")

    sfc_csv  = save_surface_csv(history,
                 out_path=os.path.join(args.out, 'fog_forecast_sfc.csv'))
    prof_csv = save_profile_csv(history,
                 out_path=os.path.join(args.out, 'fog_forecast_profiles.csv'))

    print_visibility_timeline(history)
    print_summary_report(history)

    if not args.no_plot:
        png = plot_forecast(history,
                out_png=os.path.join(args.out, 'fog_forecast.png'))

    # ── 6. Финален статус ──
    last = history[-1]
    print(f"\n[СТАТУС +{args.hours:.0f}h]  VIS={last['vis_sfc']:.0f}m  "
          f"CAT={last['cat']}  T={last['T_sfc']-273.15:.1f}°C  "
          f"RH={last['rh_sfc']*100:.0f}%\n")


if __name__ == '__main__':
    main()
