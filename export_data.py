"""export_data.py

Phase 1 of the website's data export (see Project Documents/NUFORC Website Plan.docx).

Reads the Occurred column from the nuforc_ufo_data Postgres table and writes two small,
precomputed JSON files for the site's frontend to load directly -- no database connection
happens from the public site itself:

    docs/data/sightings_by_day.json    sighting counts by calendar day-of-year (all years
                                        combined) -- feeds the "Sightings by Day" chart
    docs/data/sightings_by_year.json   raw sighting counts by year -- feeds a raw-count
                                        version of the "Sightings Over Time" chart

Phase 1 deliberately covers only what nuforc_ufo_data supports on its own. The "per
million" normalization on the yearly chart, both map sheets, and the space-launches
comparison all need population/space-launch data that isn't in Postgres yet (see the
"Open items / loose ends" section of the plan doc) -- those are Phase 2.

Only the Occurred column is read from the database. Summary, Media, and Link are never
touched here, matching the site's plan to never ship free text or unused columns to the
public site.

Run this from the repo root with the same Python environment used for scrape_nuforc.py:

    python export_data.py

It will prompt for the Postgres password the same way scrape_nuforc.py does.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _install_missing_packages(packages):
    """Install any of `packages` (import name -> pip name) that aren't already
    available. Tries `uv` first, since this project's venv is uv-managed and doesn't
    ship pip by default, falling back to plain pip for environments where uv isn't
    available."""
    for import_name, pip_name in packages.items():
        if importlib.util.find_spec(import_name) is not None:
            continue
        try:
            subprocess.check_call(["uv", "pip", "install", "--python", sys.executable, pip_name])
        except (subprocess.CalledProcessError, FileNotFoundError):
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])


_REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
}
_install_missing_packages(_REQUIRED_PACKAGES)

# --- Everything below is now safe to import normally ---
import getpass  # prompt for the Postgres password without echoing it to the terminal
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# A handful of NUFORC reports have an obviously mistyped Occurred year (e.g. 1400, 1639,
# 1714 -- almost certainly a fat-fingered entry, not a real vintage sighting). Rows before
# this year are excluded from the yearly chart as data-entry errors rather than genuine
# history; everything from here on (including the sparse-but-plausible 1940s-1960s) is kept.
MIN_PLAUSIBLE_YEAR = 1900

# --- Postgres connection settings (matches scrape_nuforc.py) ---
DB_USER = "postgres"
DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "nuforc"
TABLE_NAME = "nuforc_ufo_data"

# Output location: a docs/data/ folder at the repo root, alongside this script. The
# static site scaffold (a later step) will read JSON files from here.
REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "docs" / "data"

engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

try:
    with engine.connect():
        pass
    print(f"Connected to Postgres database '{DB_NAME}' -- proceeding with the export.")
except SQLAlchemyError as exc:
    print(f"Could not connect to Postgres:\n{exc}")
    print("Check DB_USER/DB_HOST/DB_PORT/DB_NAME above, and make sure you entered the correct password, then try again.")
    sys.exit(1)

# Column name is mixed-case in Postgres (created via pandas.to_sql from a DataFrame with
# a capitalized "Occurred" column), so it must be double-quoted here or Postgres will
# fold it to lowercase and fail to find it.
query = text(f'SELECT "Occurred" FROM {TABLE_NAME}')
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

total_rows = len(df)

# "Occurred" comes in as e.g. "09/01/2026 02:56" (MM/DD/YYYY HH:MM). Rows where it's
# missing or unparseable are dropped -- reported below rather than silently ignored.
occurred = pd.to_datetime(df["Occurred"], format="%m/%d/%Y %H:%M", errors="coerce")
dropped = occurred.isna().sum()
occurred = occurred.dropna()

print(f"Read {total_rows} rows; {dropped} had a missing/unparseable Occurred value and were excluded; {len(occurred)} used.")

# --- Sightings by Day: counts by calendar day-of-year, all years combined ---
# Grouping by (month, day) rather than day-of-year number keeps Feb 29 as its own day
# instead of shifting every later day in leap years.
by_day = (
    occurred.groupby([occurred.dt.month, occurred.dt.day])
    .size()
    .rename_axis(["month", "day"])
    .reset_index(name="count")
)
# Sort in calendar order (Jan 1 -> Dec 31), not the default month/day sort, which would
# already match here but is made explicit for clarity.
by_day = by_day.sort_values(["month", "day"])
by_day_labels = [f"{m:02d}-{d:02d}" for m, d in zip(by_day["month"], by_day["day"])]

sightings_by_day = {
    "date": by_day_labels,
    "count": by_day["count"].tolist(),
}

# --- Sightings by Year: raw counts by year ---
implausible = occurred[occurred.dt.year < MIN_PLAUSIBLE_YEAR]
if len(implausible):
    bad_years = sorted(implausible.dt.year.unique().tolist())
    print(
        f"Excluding {len(implausible)} row(s) with an Occurred year before "
        f"{MIN_PLAUSIBLE_YEAR} as data-entry errors (years seen: {bad_years})."
    )
occurred_for_yearly = occurred[occurred.dt.year >= MIN_PLAUSIBLE_YEAR]

by_year = occurred_for_yearly.dt.year.value_counts().sort_index()
current_year = datetime.now(timezone.utc).year

sightings_by_year = {
    "year": by_year.index.tolist(),
    "count": by_year.values.tolist(),
    # The current calendar year is still in progress at export time, so its bar will
    # look artificially low compared to a complete year -- the frontend should flag it
    # (an asterisk, a dotted bar, etc.) rather than let it read as a real decline.
    "partial_year": current_year if current_year in by_year.index else None,
    "generated_at": datetime.now(timezone.utc).isoformat(),
}

# --- Write output ---
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with open(OUTPUT_DIR / "sightings_by_day.json", "w") as f:
    json.dump(sightings_by_day, f, indent=2)

with open(OUTPUT_DIR / "sightings_by_year.json", "w") as f:
    json.dump(sightings_by_year, f, indent=2)

print(f"Wrote {OUTPUT_DIR / 'sightings_by_day.json'} ({len(by_day_labels)} days)")
print(f"Wrote {OUTPUT_DIR / 'sightings_by_year.json'} ({len(sightings_by_year['year'])} years)")
