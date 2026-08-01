# -*- coding: utf-8 -*-
"""
pairing.py
==========
Единствен източник за сдвояване наблюдение↔модел и за откриване на
епизоди. Импортира се от verify_cases.py, iem_fetcher.py и
ogimet_fetcher.py — трите места, които преди имаха по своя реализация.

Основен принцип
---------------
Обхождаме НАБЛЮДЕНИЯТА, не моделните записи. Наблюдението е истината,
срещу която мерим; то не бива да зависи от това дали моделът е решил да
пише точно в този миг. Старата логика обхождаше модела, затова METAR-ите
на кръгъл час винаги печелеха, а половинчасовите се губеха (при LBGO —
47 % от наблюденията в нощта).

Епизодите се връщат като ВРЕМЕВИ ИНТЕРВАЛИ, не като индекси. Старият код
броеше позиции в списък, което мълчаливо обвързваше праговете с
резолюцията на изхода: `EVENT_MIN_HRS = 2` значеше „2 часа" само докато
изходът е часов.

Съвместимост
------------
При часов моделен изход и часови наблюдения резултатът е идентичен със
старата реализация. Виж тестовете в края на файла (T4).
"""

from datetime import datetime, timedelta

# ──────────────────────────────────────────────────────────────
# Константи
# ──────────────────────────────────────────────────────────────

# Каденца на моделния изход [мин].
OUTPUT_INTERVAL_MIN = 30

# Резервен толеранс, ако каденцата не може да се изведе (< 2 записа).
FALLBACK_PAIR_DT_S = OUTPUT_INTERVAL_MIN * 60 // 2

# Праг за МОДЕЛЕН епизод — по времетраене.
# Старо: EVENT_MIN_HRS = 2 последователни часови записа = 1.0 h размах.
EVENT_MIN_DUR_H = 1.0

# Праг за НАБЛЮДАВАН епизод — по брой проби.
# Старо: EVENT_MIN_HRS_OBS = 1 запис.
EVENT_MIN_OBS_N = 1

# Максимална пролука между съседни проби в един епизод.
# Старо: несдвоеният моделен час даваше False и късаше епизода; при
# часови наблюдения пролука от 2 h къса, от 1 h — не.
MAX_GAP_H = 1.5

# Нощен прозорец за събитийната метрика.
# Старо: `not (EVENT_END_UTC < hh < 16)` при EVENT_END_UTC = 7, тоест
# отпадат цели часове 8..15 → реалната граница е 08:00, не 07:00.
# Името и коментарът в стария код заблуждаваха; тук е явно.
# ВНИМАНИЕ: това е ЗАПАЗЕНО поведение, не поправено. Дали изгревната
# карантина още е нужна е тема за Етап 4.
WIN_END_UTC   = 8.0    # изключващо
WIN_START_UTC = 16.0   # включващо


# ──────────────────────────────────────────────────────────────
# Прозорец
# ──────────────────────────────────────────────────────────────

def in_night_window(t: datetime) -> bool:
    """
    Дали моментът попада в нощния прозорец за събитийната метрика.
    Грид-независимо: 07:30 влиза, 08:00 — не.
    """
    h = t.hour + t.minute / 60.0 + t.second / 3600.0
    return (h < WIN_END_UTC) or (h >= WIN_START_UTC)


# ──────────────────────────────────────────────────────────────
# Сдвояване
# ──────────────────────────────────────────────────────────────

def infer_max_dt_s(model_times):
    """
    Половин моделна стъпка, изведена от самите записи (медиана на
    разстоянията — устойчива на единичен пропуск).

    Толерансът СЛЕДВА данните, вместо да е закована константа. Иначе
    смяна на каденцата изисква и ръчна смяна на прага, а разминаването
    им е тихо: при часова история и праг 900 s последното наблюдение
    остава несдвоено без нито едно съобщение.
    """
    if len(model_times) < 2:
        return FALLBACK_PAIR_DT_S
    ts = sorted(model_times)
    d = sorted((ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1))
    med = d[len(d) // 2] if len(d) % 2 else 0.5 * (d[len(d)//2 - 1] + d[len(d)//2])
    return int(med / 2)


def pair_obs_to_model(obs_times, model_times, max_dt_s=None):
    """
    Сдвоява всяко наблюдение с най-близкия свободен моделен запис.

    obs_times    : списък datetime (сортиран или не)
    model_times  : списък datetime
    max_dt_s     : максимално разстояние [s]; None → половин моделна стъпка

    Връща списък (i_obs, i_mod, dt_s), сортиран по време на наблюдението.

    Гаранции
    --------
    * всяко наблюдение участва най-много веднъж
    * всеки моделен запис обслужва най-много едно наблюдение
    * детерминистично при равни разстояния (по-ранното наблюдение печели,
      после по-ранният моделен запис)

    Забележка: глобално лакомо по |dt|, не по реда на наблюденията.
    При равномерни решетки резултатът е тривиално 1:1; лакомият избор
    има значение само при неравномерни пропуски.
    """
    if max_dt_s is None:
        max_dt_s = infer_max_dt_s(model_times)

    cands = []
    for i, ot in enumerate(obs_times):
        for j, mt in enumerate(model_times):
            d = abs((ot - mt).total_seconds())
            if d <= max_dt_s:
                cands.append((d, i, j))
    cands.sort()          # по d, после i, после j — детерминистично

    used_obs, used_mod, out = set(), set(), []
    for d, i, j in cands:
        if i in used_obs or j in used_mod:
            continue
        used_obs.add(i)
        used_mod.add(j)
        out.append((i, j, d))

    out.sort(key=lambda r: obs_times[r[0]])
    return out


# ──────────────────────────────────────────────────────────────
# Епизоди
# ──────────────────────────────────────────────────────────────

def episodes(times, flags, min_count=None, min_dur_h=None,
             max_gap_h=MAX_GAP_H):
    """
    Намира епизоди като времеви интервали.

    times     : списък datetime (възходящо)
    flags     : списък bool, същата дължина
    min_count : минимален брой проби (или None)
    min_dur_h : минимално времетраене t_end − t_start [h] (или None)
    max_gap_h : пролука над която епизодът се къса [h]

    Връща списък (t_start, t_end, n_samples).

    Прагът е ИЛИ по брой, ИЛИ по времетраене, ИЛИ и двете — според това
    кои аргументи са зададени. Моделните епизоди се мерят по времетраене
    (грид-независимо), наблюдаваните — по брой проби, защото едно
    наблюдение с мъгла си е събитие.
    """
    if len(times) != len(flags):
        raise ValueError("times и flags с различна дължина")

    runs, cur = [], []
    for k, (t, f) in enumerate(zip(times, flags)):
        if not f:
            if cur:
                runs.append(cur); cur = []
            continue
        if cur:
            gap_h = (t - times[cur[-1]]).total_seconds() / 3600.0
            if gap_h > max_gap_h:
                runs.append(cur); cur = []
        cur.append(k)
    if cur:
        runs.append(cur)

    out = []
    for r in runs:
        t0, t1, n = times[r[0]], times[r[-1]], len(r)
        if min_count is not None and n < min_count:
            continue
        if min_dur_h is not None:
            if (t1 - t0).total_seconds() / 3600.0 < min_dur_h - 1e-9:
                continue
        out.append((t0, t1, n))
    return out


def onset_offset_h(model_eps, obs_eps):
    """
    Разлика между началото на първия моделен и първия наблюдаван епизод
    в часове (положително = моделът закъснява). None ако липсва един от
    двата.
    """
    if not model_eps or not obs_eps:
        return None
    return (model_eps[0][0] - obs_eps[0][0]).total_seconds() / 3600.0


# ══════════════════════════════════════════════════════════════
# Приемателни тестове
# ══════════════════════════════════════════════════════════════

def _legacy_pairs(model_times, obs_times):
    """Старата логика от verify_cases._hourly_pairs — за сравнение в T4."""
    out = []
    for j, t in enumerate(model_times):
        best, bdt = None, 1801
        for i, ot in enumerate(obs_times):
            d = abs((ot - t).total_seconds())
            if d < bdt:
                best, bdt = i, d
        out.append((j, best))
    return out


def _legacy_episodes(series, min_len):
    """Старата _episodes — брои индекси."""
    eps, s = [], None
    for i, v in enumerate(list(series) + [False]):
        if v and s is None:
            s = i
        elif not v and s is not None:
            if i - s >= min_len:
                eps.append((s, i - 1))
            s = None
    return eps


def _run_tests():
    from datetime import timezone
    UTC = timezone.utc
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail else ""))
        if not cond:
            ok = False

    base = datetime(2024, 12, 30, 18, 0, tzinfo=UTC)
    mod30 = [base + timedelta(minutes=30 * k) for k in range(31)]   # 18:00–09:00
    obs30 = [base + timedelta(minutes=30 * k) for k in range(31)]
    obs60 = [base + timedelta(hours=k) for k in range(16)]
    mod60 = [base + timedelta(hours=k) for k in range(16)]

    print("\nT1  всяко наблюдение — точно една двойка")
    p = pair_obs_to_model(obs30, mod30)
    check("30-мин обси срещу 30-мин модел", len(p) == len(obs30),
          f"{len(p)}/{len(obs30)}")
    check("нито един обс два пъти", len({r[0] for r in p}) == len(p))

    print("\nT2  максимално разстояние в двойка ≤ 15 мин")
    check("изведен толеранс от 30-мин решетка", infer_max_dt_s(mod30) == 900,
          f"{infer_max_dt_s(mod30)} s")
    check("изведен толеранс от часова решетка", infer_max_dt_s(mod60) == 1800,
          f"{infer_max_dt_s(mod60)} s")
    check("всички двойки", all(r[2] <= 900 for r in p),
          f"max={max(r[2] for r in p):.0f} s")

    print("\nT3  нито един моделен запис не обслужва две наблюдения")
    check("уникални моделни индекси", len({r[1] for r in p}) == len(p))
    # плътен случай: обси на 15 мин срещу 30-мин модел → част остават несдвоени
    obs15 = [base + timedelta(minutes=15 * k) for k in range(61)]
    p15 = pair_obs_to_model(obs15, mod30)
    check("при обси на 15 мин моделът не се преизползва",
          len({r[1] for r in p15}) == len(p15),
          f"{len(p15)} двойки от {len(obs15)} обса")

    print("\nT4  бит-в-бит съвпадение със старата логика при часов вход")
    new = pair_obs_to_model(obs60, mod60)   # толерансът се извежда = 1800 s
    leg = [(i, j) for j, i in _legacy_pairs(mod60, obs60) if i is not None]
    check("същите двойки", sorted((i, j) for i, j, _ in new) == sorted(leg),
          f"{len(new)} срещу {len(leg)}")

    # епизоди: моделен праг
    flags = [False, True, True, False, True, False, True, True, True, False,
             False, False, False, False, False, False]
    e_new = episodes(mod60, flags, min_dur_h=EVENT_MIN_DUR_H, max_gap_h=MAX_GAP_H)
    e_leg = _legacy_episodes(flags, 2)
    check("моделни епизоди", len(e_new) == len(e_leg),
          f"нов {len(e_new)} / стар {len(e_leg)}")
    check("същите начала",
          [t0 for t0, _, _ in e_new] == [mod60[s] for s, _ in e_leg])

    # епизоди: наблюдаван праг
    o_new = episodes(mod60, flags, min_count=EVENT_MIN_OBS_N, max_gap_h=MAX_GAP_H)
    o_leg = _legacy_episodes(flags, 1)
    check("наблюдавани епизоди", len(o_new) == len(o_leg),
          f"нов {len(o_new)} / стар {len(o_leg)}")

    print("\nT5  пролука къса епизода както преди")
    sparse_t = [base, base + timedelta(hours=1), base + timedelta(hours=3)]
    e = episodes(sparse_t, [True, True, True], min_count=1, max_gap_h=1.5)
    check("2 h пролука → два епизода", len(e) == 2, f"{len(e)}")
    e = episodes(sparse_t, [True, True, True], min_count=1, max_gap_h=3.5)
    check("широка толерантност → един", len(e) == 1, f"{len(e)}")

    print("\nT6  нощен прозорец")
    d = datetime(2024, 12, 31, tzinfo=UTC)
    cases = [(7, 30, True), (7, 59, True), (8, 0, False), (15, 59, False),
             (16, 0, True), (23, 30, True), (0, 30, True)]
    for hh, mm, want in cases:
        got = in_night_window(d.replace(hour=hh, minute=mm))
        check(f"{hh:02d}:{mm:02d} → {'вътре' if want else 'вън'}", got == want)

    print("\nT7  onset")
    m_eps = episodes(mod60, [False, False, True, True, False] + [False] * 11,
                     min_dur_h=1.0)
    o_eps = episodes(mod60, [False, True, True, False, False] + [False] * 11,
                     min_count=1)
    check("моделът закъснява с 1 h", onset_offset_h(m_eps, o_eps) == 1.0,
          f"{onset_offset_h(m_eps, o_eps)}")

    print("\n" + ("ВСИЧКИ ТЕСТОВЕ МИНАХА" if ok else "ИМА ПАДНАЛИ ТЕСТОВЕ"))
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_tests() else 1)
