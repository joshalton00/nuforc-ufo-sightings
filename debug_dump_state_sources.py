"""debug_dump_state_sources.py

Same idea as debug_dump_sources.py (which handles the national population data), but for
the three state-level Census sources import_state_population.py will eventually use. No
Postgres connection, no password prompt -- this just downloads the raw files to
population_debug_state/ so they can be inspected directly before any parsing code gets
written.

The state-level files live at different URLs than the national ones, and past experience
this project (see population_parsers.py's docstrings) is that Census's documented file
names/casing don't always match what's actually on the server -- e.g. the national
2010-2019 source turned out to be lowercase na-est2019-01.xlsx, not the NA-EST2019-01.xlsx
the documentation implied. So this script also tries a couple of casing fallbacks per file
rather than giving up on the first 404.

Run from the repo root:

    python debug_dump_state_sources.py
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

# Each entry lists one or more candidate URLs to try in order -- the first one that
# returns 200 is saved and the rest are skipped. Scoped to 2000-2026 per Josh's call:
# these three vintages are all clean Excel files (the pre-2000 state sources are a PDF
# and nine unverified decade text files, judged not worth the added fragility).
SOURCES = {
    "2000-2010-state.xls": [
        "https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/state/st-est00int-01.xls",
    ],
    "2010-2019-state.xlsx": [
        "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/state/totals/nst-est2019-01.xlsx",
        "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/state/totals/NST-EST2019-01.xlsx",
    ],
    "2020-2026-state.xlsx": [
        "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/state/totals/NST-EST2025-POP.xlsx",
        "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/state/totals/nst-est2025-pop.xlsx",
    ],
}

OUT_DIR = Path(__file__).resolve().parent / "population_debug_state"
OUT_DIR.mkdir(exist_ok=True)

for filename, urls in SOURCES.items():
    out_path = OUT_DIR / filename
    last_error = None
    for url in urls:
        print(f"Downloading {filename}\n  {url}")
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            print(f"  Failed ({exc}) -- trying next candidate URL if any remain.")
            last_error = exc
            continue
        out_path.write_bytes(response.content)
        print(f"  Saved {len(response.content):,} bytes to {out_path}")
        last_error = None
        break
    if last_error is not None:
        print(f"  Could not download {filename} from any candidate URL. Last error: {last_error}")

print(f"\nDone. Files are in {OUT_DIR}")
