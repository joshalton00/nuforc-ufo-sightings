"""export_national_population.py

Exports the national us_population_by_year Postgres table (see import_population.py) to
a small JSON file for the site's frontend to load directly -- no database connection
happens from the public site itself, same as export_data.py.

    docs/data/population_national.json   national population by year, 1900-2026

This is a separate script from export_data.py deliberately: the sightings data and the
national population data update on different schedules (a fresh NUFORC scrape vs. a new
Census release, maybe once a year), so each can be re-run and its output file updated on
its own without touching the other's.

No join against nuforc_ufo_data happens here, or anywhere server-side. The site's
architecture keeps aggregation precomputed but leaves cheap arithmetic (like dividing
sightings by population to get a per-million figure) to the frontend at render time --
script.js loads this file and sightings_by_year.json separately and does that division
itself, which is what actually keeps this export independent of the sightings one. See
Project Documents/NUFORC Website Plan.docx for the fuller architecture note.

Run this from the repo root with the same Python environment used for the other scripts:

    python export_national_population.py

It will prompt for the Postgres password the same way those scripts do.
"""

import importlib.util
import json
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

# --- Postgres connection settings (matches scrape_nuforc.py / export_data.py) ---
DB_USER = "postgres"
DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "nuforc"
TABLE_NAME = "us_population_by_year"

# Same output location export_data.py writes to.
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

# Columns (year, population, source_vintage) are all lowercase -- import_population.py
# loaded this table from a DataFrame with lowercase column names, so unlike
# nuforc_ufo_data's "Occurred" this doesn't need double-quoting to avoid Postgres folding.
query = text(f"SELECT year, population, source_vintage FROM {TABLE_NAME} ORDER BY year")
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

if df.empty:
    print(
        f"'{TABLE_NAME}' is empty -- nothing to export. Run import_population.py first "
        "to load it."
    )
    sys.exit(1)

population_national = {
    "year": df["year"].tolist(),
    "population": df["population"].tolist(),
    "source_vintage": df["source_vintage"].tolist(),
    "generated_at": datetime.now(timezone.utc).isoformat(),
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
out_path = OUTPUT_DIR / "population_national.json"
with open(out_path, "w") as f:
    json.dump(population_national, f, indent=2)

print(f"Wrote {out_path} ({len(df)} years, {df['year'].min()}-{df['year'].max()})")
