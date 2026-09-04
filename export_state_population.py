"""export_state_population.py

Exports the us_state_population_by_year Postgres table (see import_state_population.py)
to a small JSON file for the site's frontend to load directly -- no database connection
happens from the public site itself, same as export_data.py.

    docs/data/population_state.json   population by state and year, 2000-2025

This is a separate script from both export_data.py and export_national_population.py
deliberately: the sightings data, the national population data, and the state population
data can each get a fresh source release (a new NUFORC scrape, a new Census vintage) on
their own schedule, so each export script can be re-run and its output file updated
independently of the other two.

No join against nuforc_ufo_data happens here, or anywhere server-side -- same reasoning
as export_national_population.py: the frontend loads this file alongside whatever
sightings-by-state-and-year data the map sheets need and does the cheap per-100K division
itself at render time. See Project Documents/NUFORC Website Plan.docx for the fuller
architecture note.

Output is flat parallel arrays (state/year/population/source_vintage), matching the shape
export_data.py and export_national_population.py already use, rather than nested by
state -- keeps every export in this project shaped the same way; script.js can group it
by state client-side in a couple of lines if that's a more convenient shape to chart from.

Run this from the repo root with the same Python environment used for the other scripts:

    python export_state_population.py

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
TABLE_NAME = "us_state_population_by_year"

# Same output location export_data.py / export_national_population.py write to.
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

# Columns (state, year, population, source_vintage) are all lowercase -- same reasoning
# as export_national_population.py, no double-quoting needed.
query = text(f"SELECT state, year, population, source_vintage FROM {TABLE_NAME} ORDER BY state, year")
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

if df.empty:
    print(
        f"'{TABLE_NAME}' is empty -- nothing to export. Run import_state_population.py "
        "first to load it."
    )
    sys.exit(1)

population_state = {
    "state": df["state"].tolist(),
    "year": df["year"].tolist(),
    "population": df["population"].tolist(),
    "source_vintage": df["source_vintage"].tolist(),
    "generated_at": datetime.now(timezone.utc).isoformat(),
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
out_path = OUTPUT_DIR / "population_state.json"
with open(out_path, "w") as f:
    json.dump(population_state, f, indent=2)

states_found = df["state"].nunique()
print(f"Wrote {out_path} ({states_found} states/DC, {df['year'].min()}-{df['year'].max()})")
