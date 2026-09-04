"""debug_dump_sources.py

Downloads the same four Census source files import_population.py uses, and just saves
them to disk instead of parsing them. No Postgres connection, no password prompt --
this exists purely so the raw files can be inspected directly (population_debug/) after
a parsing failure, rather than guessing at a fix blind.

Run from the repo root:

    python debug_dump_sources.py
"""

import importlib.util
import subprocess
import sys
from pathlib import Path


def _install_missing_packages(packages):
    for import_name, pip_name in packages.items():
        if importlib.util.find_spec(import_name) is not None:
            continue
        try:
            subprocess.check_call(["uv", "pip", "install", "--python", sys.executable, pip_name])
        except (subprocess.CalledProcessError, FileNotFoundError):
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])


_install_missing_packages({"requests": "requests"})

import requests  # noqa: E402

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nuforc-project-population-import/1.0)"}
REQUEST_TIMEOUT = 60

SOURCES = {
    "1900-1999.txt": "https://www2.census.gov/programs-surveys/popest/tables/1900-1980/national/totals/popclockest.txt",
    "2000-2010.xls": "https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/national/us-est00int-01.xls",
    "2010-2019.xlsx": "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/national/totals/na-est2019-01.xlsx",
    "2020-2026.xlsx": "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/national/totals/NA-EST2025-POP.xlsx",
}

OUT_DIR = Path(__file__).resolve().parent / "population_debug"
OUT_DIR.mkdir(exist_ok=True)

for filename, url in SOURCES.items():
    print(f"Downloading {filename}\n  {url}")
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    out_path = OUT_DIR / filename
    out_path.write_bytes(response.content)
    print(f"  Saved {len(response.content):,} bytes to {out_path}")

print(f"\nDone. Files are in {OUT_DIR}")
