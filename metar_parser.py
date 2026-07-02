"""
metar_parser.py
===============
Парсер за METAR съобщения от LBSF и другите летища.
Поддържа AUTO формат, вариабилен вятър (240V330), нестандартни
токени като OVC200///, и непоследователни токени.

Стратегия: сканиране напред (не sequential) за T/Td и QNH,
           което е устойчиво на непознати токени в средата.
"""

import re
import numpy as np
from datetime import datetime


def parse_metar(raw: str) -> dict:
    """
    Парсира METAR низ и връща речник с декодирани елементи.
    """
    result = {
        'station': None, 'day': None, 'hour': None, 'minute': None,
        'wind_dir': None, 'wind_speed': None, 'wind_gust': None,
        'visibility': None, 'weather': [],
        'ceiling': None, 'vv': None,
        'T': None, 'Td': None, 'QNH': None,
        'auto': False, 'raw': raw.strip(),
    }

    tokens = raw.strip().split()
    if not tokens:
        return result

    idx = 0

    # ── Идентификатор METAR/SPECI ──
    if tokens[idx] in ('METAR', 'SPECI'):
        idx += 1

    # ── Станция ──
    if idx < len(tokens) and len(tokens[idx]) == 4 and tokens[idx].isalpha():
        result['station'] = tokens[idx]; idx += 1

    # ── Дата/Час ──
    if idx < len(tokens):
        m = re.match(r'^(\d{2})(\d{2})(\d{2})Z$', tokens[idx])
        if m:
            result['day'], result['hour'], result['minute'] = int(m[1]), int(m[2]), int(m[3])
            idx += 1

    # ── AUTO / COR ──
    if idx < len(tokens) and tokens[idx] in ('AUTO', 'COR'):
        result['auto'] = True; idx += 1

    # ── Вятър ──
    if idx < len(tokens):
        m = re.match(r'^(VRB|\d{3})(\d{2,3})(G(\d{2,3}))?KT$', tokens[idx])
        if m:
            result['wind_dir']   = None if m[1] == 'VRB' else int(m[1])
            result['wind_speed'] = int(m[2])
            result['wind_gust']  = int(m[4]) if m[4] else None
            idx += 1

    # ── Вариабилен вятър (240V330) — прескачаме ──
    if idx < len(tokens) and re.match(r'^\d{3}V\d{3}$', tokens[idx]):
        idx += 1

    # ── Видимост ──
    if idx < len(tokens):
        m = re.match(r'^(\d{4})$', tokens[idx])
        if m:
            vis = int(m[1])
            result['visibility'] = 10000 if vis == 9999 else vis
            idx += 1
        elif tokens[idx] == 'CAVOK':
            result['visibility'] = 10000; idx += 1

    # ── RVR — прескачаме (R28/0600U и подобни) ──
    while idx < len(tokens) and re.match(r'^R\d{2}[LCR]?/', tokens[idx]):
        idx += 1

    # ── Явления ──
    wx_codes = {
        'FG', 'BR', 'HZ', 'RA', 'DZ', 'SN', 'MIFG', 'BCFG',
        'PRFG', 'FZFG', 'FZRA', 'TSGR', 'TSRA', 'TS', 'SG',
        'PL', 'GR', 'GS', 'UP', 'BLSN', 'DRSN',
        '+RA', '-RA', '+SN', '-SN', '+TSRA', '-TSRA',
        '+FZRA', '-FZRA', '+DZ', '-DZ',
    }
    while idx < len(tokens):
        t = tokens[idx]
        if t in wx_codes or t.lstrip('+-') in {c.lstrip('+-') for c in wx_codes}:
            result['weather'].append(t); idx += 1
        else:
            break

    # ── Облачност / VV ──
    while idx < len(tokens):
        t = tokens[idx]
        m_vv = re.match(r'^VV(\d{3})$', t)
        # OVC200/// или BKN006 или FEW032TCU — match само на началото
        m_cl = re.match(r'^(FEW|SCT|BKN|OVC|SKC|NSC|NCD|CLR)(\d{3})?', t)
        if m_vv:
            result['vv'] = int(m_vv[1]) * 100
            idx += 1
        elif m_cl and m_cl[1] not in ('SKC', 'NSC', 'NCD', 'CLR'):
            if m_cl[2]:
                alt_ft = int(m_cl[2]) * 100
                cover  = m_cl[1]
                if cover in ('BKN', 'OVC'):
                    if result['ceiling'] is None or alt_ft < result['ceiling']:
                        result['ceiling'] = alt_ft
            idx += 1
        elif m_cl:   # SKC, NSC, NCD, CLR
            idx += 1
        else:
            break

    # ── T/Td — сканираме напред (устойчиво на непознати токени) ──
    # Търсим паттерн XX/XX или MXX/MXX навсякъде до края
    for i in range(idx, len(tokens)):
        m = re.match(r'^(M?)(\d{2})/(M?)(\d{2})$', tokens[i])
        if m:
            result['T']  = (-1 if m[1] == 'M' else 1) * int(m[2])
            result['Td'] = (-1 if m[3] == 'M' else 1) * int(m[4])
            idx = i + 1
            break

    # ── QNH ──
    for i in range(idx, len(tokens)):
        m = re.match(r'^Q(\d{4})$', tokens[i])
        if m:
            result['QNH'] = int(m[1]); idx = i + 1; break
        m = re.match(r'^A(\d{4})$', tokens[i])   # алтиметър (US формат)
        if m:
            result['QNH'] = round(int(m[1]) * 0.338639); idx = i + 1; break

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Корекция на WRF/ICON профила с METAR данни
# ──────────────────────────────────────────────────────────────────────────────

def apply_metar_correction(profile: dict, metar: dict) -> dict:
    """Коригира приземния слой на профила спрямо METAR."""
    from fog_model import sat_vapor_pressure, eps_r

    prof = {k: v.copy() if isinstance(v, np.ndarray) else v
            for k, v in profile.items()}

    if metar.get('T') is None:
        print("[METAR] Не е намерена температура — пропускам корекция.")
        prof.setdefault('ql_init', np.zeros_like(prof['T']))
        return prof

    T_obs_K  = metar['T']  + 273.15
    Td_obs_K = metar['Td'] + 273.15 if metar.get('Td') is not None else None

    # Температурна корекция
    dT_corr = T_obs_K - prof['T'][0]
    weight  = np.exp(-prof['z'] / 50.0)
    prof['T'] += dT_corr * weight
    print(f"[METAR] Температурна корекция: ΔT={dT_corr:+.1f} K @ z=0")

    # Влажностна корекция
    if Td_obs_K is not None:
        es_obs = sat_vapor_pressure(Td_obs_K)
        p_sfc  = prof['p'][0]
        qv_obs = eps_r * es_obs / (p_sfc - es_obs)
        dqv    = qv_obs - prof['qv'][0]
        prof['qv'] += dqv * weight
        prof['qv'] = np.maximum(prof['qv'], 1e-8)
        print(f"[METAR] Влажностна корекция: qv_obs={qv_obs*1000:.2f} g/kg")

    # Вятър
    if metar.get('wind_speed') is not None:
        spd_ms = metar['wind_speed'] * 0.5144
        wd     = metar['wind_dir'] if metar['wind_dir'] is not None else 0
        u_obs  = -spd_ms * np.sin(np.deg2rad(wd))
        v_obs  = -spd_ms * np.cos(np.deg2rad(wd))
        prof['u'] += (u_obs - prof['u'][0]) * weight
        prof['v'] += (v_obs - prof['v'][0]) * weight
        print(f"[METAR] Вятър: {wd}°/{metar['wind_speed']}kt "
              f"→ u={u_obs:.1f}, v={v_obs:.1f} m/s")

    # Начална мъгла
    wx = metar.get('weather', [])
    if any(w in wx for w in ('FG', 'FZFG', 'MIFG', 'BCFG', 'PRFG')):
        vis_m = metar.get('visibility') or 200
        lwc_init_g = (144.7 / max(vis_m, 10)) ** (1.0 / 0.65)
        lwc_init   = lwc_init_g / 1000.0
        fog_weight  = np.exp(-prof['z'] / 50.0)
        prof['ql_init'] = np.maximum(lwc_init * fog_weight, 0.0)
        print(f"[METAR] Мъгла! VIS={vis_m}m → LWC_init≈{lwc_init_g:.3f} g/m³")
    else:
        prof['ql_init'] = np.zeros_like(prof['T'])

    return prof


# ──────────────────────────────────────────────────────────────────────────────
# Тест
# ──────────────────────────────────────────────────────────────────────────────

EXAMPLE_METARS = {
    'fog':    "METAR LBSF 151820Z 09003KT 0200 FG VV001 03/03 Q1018 NOSIG=",
    'auto':   "METAR LBPD 020600Z AUTO 28003KT 240V330 9999 OVC200/// 21/18 Q1011 NOSIG",
    'mist':   "METAR LBSF 011820Z 09002KT 6000 SCT006 BKN066 17/15 Q1014 TEMPO 4000 BR BKN006",
    'tcu':    "METAR LBWN 020600Z 26005KT 9999 FEW032TCU SCT040 24/19 Q1010 NOSIG",
}

def decode_and_print(raw: str) -> dict:
    m = parse_metar(raw)
    print(f"\n{'─'*50}")
    print(f"Станция : {m['station']}   AUTO={m['auto']}")
    print(f"Час UTC : {m['day']:02d} {m['hour']:02d}:{m['minute']:02d}")
    print(f"Вятър   : {m['wind_dir']}° / {m['wind_speed']} kt")
    print(f"VIS     : {m['visibility']} m")
    print(f"Явления : {', '.join(m['weather']) or '—'}")
    print(f"Таван   : {m['ceiling']} ft    VV: {m['vv']} ft")
    print(f"T / Td  : {m['T']}°C / {m['Td']}°C")
    print(f"QNH     : {m['QNH']} hPa")
    return m

if __name__ == "__main__":
    print("=== Тест на METAR парсера ===")
    for name, raw in EXAMPLE_METARS.items():
        print(f"\n[{name}] {raw}")
        m = decode_and_print(raw)
        assert m['T'] is not None, f"T не е намерена за {name}!"
    print("\n✓ Всички тестове минаха")
