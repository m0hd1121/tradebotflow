#!/usr/bin/env python3
"""Download historical EURUSD bars from MT5 or convert from CSV formats.

Usage (with MetaTrader5 Python package on Windows/Wine VPS):
  python scripts/download_historical.py --mt5 --from 2020-01-01 --to 2024-12-31

Usage (convert existing CSV to standard format):
  python scripts/download_historical.py --convert path/to/raw.csv --timeframe M5
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


STANDARD_HEADERS = ["time_utc", "open", "high", "low", "close", "tick_volume"]


def _download_mt5(symbol: str, from_dt: datetime, to_dt: datetime,
                  out_dir: Path) -> None:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("MetaTrader5 package not available — install on Windows VPS")

    if not mt5.initialize():
        sys.exit(f"MT5 init failed: {mt5.last_error()}")

    timeframe_map = {
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1":  mt5.TIMEFRAME_H1,
    }

    for tf_name, tf_const in timeframe_map.items():
        print(f"Downloading {symbol} {tf_name} from {from_dt.date()} to {to_dt.date()}...")
        rates = mt5.copy_rates_range(symbol, tf_const, from_dt, to_dt)
        if rates is None or len(rates) == 0:
            print(f"  No data for {tf_name}")
            continue

        out_file = out_dir / f"{symbol}_{tf_name}.csv"
        with open(out_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(STANDARD_HEADERS)
            for r in rates:
                dt = datetime.fromtimestamp(r["time"], tz=timezone.utc)
                writer.writerow([
                    dt.strftime("%Y-%m-%d %H:%M:%S"),
                    f"{r['open']:.5f}", f"{r['high']:.5f}",
                    f"{r['low']:.5f}", f"{r['close']:.5f}",
                    str(r["tick_volume"]),
                ])
        print(f"  Saved {len(rates)} bars to {out_file}")

    mt5.shutdown()


def _convert_csv(raw_path: str, timeframe: str, symbol: str, out_dir: Path) -> None:
    raw = Path(raw_path)
    if not raw.exists():
        sys.exit(f"File not found: {raw_path}")

    out_file = out_dir / f"{symbol}_{timeframe}.csv"
    count = 0

    with open(raw) as src, open(out_file, "w", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.writer(dst)
        writer.writerow(STANDARD_HEADERS)

        for row in reader:
            # Support common column name variants
            time_val = (row.get("time") or row.get("Date") or
                        row.get("datetime") or row.get("Datetime", ""))
            open_v  = row.get("open") or row.get("Open", "0")
            high_v  = row.get("high") or row.get("High", "0")
            low_v   = row.get("low")  or row.get("Low", "0")
            close_v = row.get("close") or row.get("Close", "0")
            vol_v   = row.get("tick_volume") or row.get("Volume") or row.get("volume", "0")
            writer.writerow([time_val, open_v, high_v, low_v, close_v, vol_v])
            count += 1

    print(f"Converted {count} bars to {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download or convert historical EURUSD data")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--out-dir", default="data")
    parser.add_argument("--mt5", action="store_true", help="Download from MT5")
    parser.add_argument("--from", dest="from_date", default="2020-01-01")
    parser.add_argument("--to", dest="to_date",
                        default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--convert", metavar="CSV_FILE",
                        help="Convert existing CSV to standard format")
    parser.add_argument("--timeframe", default="M5",
                        choices=["M5", "M15", "H1"])
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.convert:
        _convert_csv(args.convert, args.timeframe, args.symbol, out_dir)
    elif args.mt5:
        from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        to_dt   = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
        _download_mt5(args.symbol, from_dt, to_dt, out_dir)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
