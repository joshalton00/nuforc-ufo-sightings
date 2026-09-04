"""export_sightings_by_state.py

Exports NUFORC sighting counts grouped by (US state, year) from the nuforc_ufo_data
Postgres table, for the Geographic Slice Map and Hex Map sheets:

    docs/data/sightings_by_state.json   sighting counts by state and year

This is a separate script from export_data.py and the population exports, following the
same reasoning as those (see Project Documents/NUFORC Website Plan.docx) -- the sightings
scrape and the two population tables each update on their own schedule.

The State column mixes US states with non-US regions/provinces (NUFORC accepts reports
worldwide -- see the plan doc's Data section), and this project has not been able to
directly inspect the live database to confirm its exact format (2-letter postal code vs.
full name, consistent casing, etc.) before writing this. Rather than guess a single
format and risk silently dropping most of the data, normalize_state() below accepts
either a standard USPS 2-letter code or an already-spelled-out state name, and anything
that resolves to neither the 50 states nor DC -- a foreign province, a typo, a blank
value -- is dropped and counted, with the console output reporting exactly how many rows
were kept vs. dropped so a wrong assumption here shows up immediately rather than as a
quietly wrong map later. STATE_NAMES is imported from state_population_parsers.py rather
than redefined here, so there's one source of truth for "the 50 states + DC" across both
the population and sightings pipelines.

No join against either population table happens here -- same client-side-division
architecture as the other two population exports and the Sightings Over Time toggle: the
map charts in script.js will load this file alongside population_state.json and divide
at render time.

Run this from the repo root with the same Python environment used for the other scripts:

    python export_sightings_by_state.py

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

from state_population_parsers import STATE_NAMES

# A handful of NUFORC reports have an obviously mistyped Occurred year -- same filter and
# reasoning export_data.py already uses for the yearly chart.
MIN_PLAUSIBLE_YEAR = 1900

# Standard USPS 2-letter codes -> the exact state name spellings STATE_NAMES uses (so a
# lookup here always matches the population data's spelling too). Built from STATE_NAMES
# itself further down via a hand-maintained code list, rather than guessing full-name
# spellings independently, so the two can't drift apart.
_POSTAL_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
if set(_POSTAL_CODES) != STATE_NAMES:
    # Guards against STATE_NAMES changing (a new territory added, a typo fixed) without
    # this hand-maintained postal-code table being updated to match -- would otherwise
    # silently stop recognizing whichever state fell out of sync.
    raise RuntimeError(
        "The postal-code table here and STATE_NAMES (from state_population_parsers.py) "
        "have drifted apart -- update _POSTAL_CODES to match."
    )
POSTAL_TO_STATE = {code: name for name, code in _POSTAL_CODES.items()}
# Case-insensitive lookup for the already-spelled-out-name form. Deliberately NOT
# str.title() -- "district of columbia".title() capitalizes "Of" too ("District Of
# Columbia"), which then fails to match STATE_NAMES' actual "District of Columbia"
# spelling. Lowercasing both sides sidesteps that instead of special-casing "of".
_LOWER_TO_STATE = {name.lower(): name for name in STATE_NAMES}


def normalize_state(raw):
    """Resolve a raw State cell to one of the 50 state names + "District of Columbia",
    or None if it doesn't match either form -- a non-US region/province, a typo, or a
    blank value."""
    if pd.isna(raw):
        return None
    text_val = str(raw).strip()
    if not text_val:
        return None
    upper = text_val.upper()
    if upper in POSTAL_TO_STATE:
        return POSTAL_TO_STATE[upper]
    lower = text_val.lower()
    if lower in _LOWER_TO_STATE:
        return _LOWER_TO_STATE[lower]
    return None


# --- Postgres connection settings (matches scrape_nuforc.py / export_data.py) ---
DB_USER = "postgres"
DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "nuforc"
TABLE_NAME = "nuforc_ufo_data"

# Same output location the other export scripts write to.
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

# "State" and "Occurred" are mixed-case in Postgres (created via pandas.to_sql from a
# DataFrame with capitalized columns), so double-quoting is required here -- same
# reasoning export_data.py notes for "Occurred".
query = text(f'SELECT "State", "Occurred" FROM {TABLE_NAME}')
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

total_rows = len(df)

occurred = pd.to_datetime(df["Occurred"], format="%m/%d/%Y %H:%M", errors="coerce")
unparseable_occurred = occurred.isna().sum()

state = df["State"].map(normalize_state)
unrecognized_state = state.isna().sum()

usable = pd.DataFrame({"state": state, "year": occurred.dt.year}).dropna()
implausible = usable[usable["year"] < MIN_PLAUSIBLE_YEAR]
if len(implausible):
    usable = usable[usable["year"] >= MIN_PLAUSIBLE_YEAR]

print(f"Read {total_rows} rows.")
print(f"  {unparseable_occurred} had a missing/unparseable Occurred value.")
print(f"  {unrecognized_state} had a State value that isn't a recognized US state/DC "
      f"({unrecognized_state / total_rows:.0%} of all rows) -- most likely non-US "
      f"reports (NUFORC accepts worldwide sightings), but worth a skim of the sample "
      f"below if that fraction looks off.")
if unrecognized_state > 0:
    sample = df.loc[state.isna(), "State"].value_counts().head(10)
    print("  Most common unrecognized State values:")
    for value, count in sample.items():
        print(f"    {value!r}: {count}")
if len(implausible):
    print(f"  {len(implausible)} row(s) excluded as data-entry errors (year before {MIN_PLAUSIBLE_YEAR}).")
print(f"  {len(usable)} rows usable ({len(usable) / total_rows:.0%} of all rows).")

if len(usable) == 0:
    print(
        "\nNo usable state-year rows at all -- normalize_state() may not be matching this "
        "database's actual State format. Check the sample of unrecognized values above; "
        "if it doesn't look like state names/postal codes, this needs a fix before going "
        "further."
    )
    sys.exit(1)

grouped = usable.groupby(["state", "year"]).size().reset_index(name="count")
grouped["year"] = grouped["year"].astype(int)
grouped = grouped.sort_values(["state", "year"])

states_found = grouped["state"].nunique()
print(f"\n{states_found} of {len(STATE_NAMES)} states/DC have at least one sighting.")
missing_states = STATE_NAMES - set(grouped["state"])
if missing_states:
    print(f"  No sightings at all for: {sorted(missing_states)} (plausible for low-population states -- not necessarily a bug).")

sightings_by_state = {
    "state": grouped["state"].tolist(),
    "year": grouped["year"].tolist(),
    "count": grouped["count"].tolist(),
    "generated_at": datetime.now(timezone.utc).isoformat(),
}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
out_path = OUTPUT_DIR / "sightings_by_state.json"
with open(out_path, "w") as f:
    json.dump(sightings_by_state, f, indent=2)

print(f"\nWrote {out_path} ({len(grouped)} state-year rows, {grouped['year'].min()}-{grouped['year'].max()})")
