"""
prefetch_icon_cache.py
======================
Тегли липсващите ICON профили от Open-Meteo за всичките случаи
в features.csv и ги записва в icon_cache/.

Употреба:
  python prefetch_icon_cache.py --features features.csv
                                --cache    icon_cache/
                                --hour     18
                                --fh       16

Скриптът пропуска вече кешираните файлове (идемпотентен).
При мрежова грешка печата предупреждение и продължава.
В края показва колко са изтеглени, колко са пропуснати и колко са
пропаднали.
"""

import argparse
import csv
import json
import os
import time
import numpy as np


# ──────────────────────────────────────────────────────────────
# Помощни функции
# ──────────────────────────────────────────────────────────────

def cache_path(cache_dir: str, icao: str, date_str: str,
               hour: int, fh: int) -> str:
    key = f"{icao}_{date_str}_{hour:02d}_{fh}"
    return os.path.join(cache_dir, key + ".json")


def is_valid_cache(path: str) -> bool:
    """Връща True ако файлът съществува и е валиден формат."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        cc = data.get("cc_series")
        if cc is None:
            return False
        if len(cc) == 0:
            return False
        # Стар формат: cc_series елементите имат < 5 стойности
        if len(cc[0]) < 5:
            return False
        return True
    except Exception:
        return False


def np_convert(o):
    if isinstance(o, np.integer):  return int(o)
    if isinstance(o, np.floating): return float(o)
    if isinstance(o, np.ndarray):  return o.tolist()
    raise TypeError(type(o))


# ──────────────────────────────────────────────────────────────
# Главна функция
# ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Тегли липсващи ICON профили за всичките случаи в features.csv")
    ap.add_argument("--features", default="features.csv",
                    help="Път до features.csv")
    ap.add_argument("--cache",    default="icon_cache",
                    help="Директория за кеша (icon_cache/)")
    ap.add_argument("--hour",     type=int, default=18,
                    help="Начален UTC час (по подразбиране: 18)")
    ap.add_argument("--fh",       type=int, default=16,
                    help="Forecast hours (по подразбиране: 16)")
    ap.add_argument("--dry-run",  action="store_true",
                    help="Само показва какво би изтеглило, без да тегли")
    ap.add_argument("--sleep",    type=float, default=1.0,
                    help="Пауза между заявките в секунди (по подразбиране: 1.0)")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)

    # Четем features.csv
    with open(args.features, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Уникални icao+date комбинации
    combos = sorted(set((r["icao"], r["date"]) for r in rows))
    print(f"[INFO] {len(combos)} уникални случая в {args.features}")

    # Разделяме на налични и липсващи
    to_fetch = []
    skipped  = []
    for icao, date_str in combos:
        path = cache_path(args.cache, icao, date_str, args.hour, args.fh)
        if is_valid_cache(path):
            skipped.append((icao, date_str))
        else:
            to_fetch.append((icao, date_str))

    print(f"[INFO] Вече кеширани: {len(skipped)}")
    print(f"[INFO] За теглене:    {len(to_fetch)}")

    if args.dry_run:
        print("\n[DRY-RUN] Ще се теглят:")
        for icao, date_str in to_fetch:
            print(f"  {icao}  {date_str}")
        return

    if not to_fetch:
        print("[OK] Всичко е кешираано — нищо за теглене.")
        return

    # Импортираме fetch_icon_historical от run_case
    try:
        from run_case import fetch_icon_historical
    except ImportError as e:
        print(f"[ERROR] Не може да се импортира run_case: {e}")
        print("  Уверете се, че скриптът се пуска от C:\\fogmodel")
        return

    # Теглим
    fetched = 0
    failed  = []

    for i, (icao, date_str) in enumerate(to_fetch):
        path = cache_path(args.cache, icao, date_str, args.hour, args.fh)
        pct  = (i + 1) / len(to_fetch) * 100
        print(f"  [{pct:5.1f}%] {icao}  {date_str} ... ", end="", flush=True)

        try:
            prof = fetch_icon_historical(icao, date_str,
                                         hour0=args.hour,
                                         forecast_hours=args.fh)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(prof, f, ensure_ascii=False, default=np_convert)
            fetched += 1
            print("OK")
        except Exception as e:
            failed.append((icao, date_str, str(e)))
            print(f"ГРЕШКА: {e}")

        if i < len(to_fetch) - 1:
            time.sleep(args.sleep)

    # Резюме
    print()
    print("=" * 50)
    print(f"Изтеглени:  {fetched}")
    print(f"Пропуснати: {len(skipped)}")
    print(f"Пропаднали: {len(failed)}")
    if failed:
        print("\nПропаднали случаи:")
        for icao, date_str, err in failed:
            print(f"  {icao}  {date_str}  →  {err}")
    print("=" * 50)


if __name__ == "__main__":
    main()
