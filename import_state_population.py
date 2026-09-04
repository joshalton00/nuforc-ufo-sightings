"""import_state_population.py

Sources U.S. population-by-state-by-year data for the Geographic Slice Map and Hex Map
Tableau sheets (see Project Documents/NUFORC Website Plan.docx). Scoped to 2000-2025 --
per Josh's call, this skips the pre-2000 state sources (nine unverified decade text files
for 1900-1990, plus a PDF for 1990-2000) as not worth the added fragility for this
project. The 2020-2025 vintage is Census's most recent state release and only reaches
July 1, 2025 (not 2026) -- one year short of the national series, which is fine, it's just
where Census's own release currently ends.

Three Census sources, stitched the same "later vintage wins on overlap" way as the
national data (see import_population.py's docstring for that reasoning):

    2000-2010   State Intercensal Tables: 2000-2010 (Excel)
    2010-2019   State Population Totals: 2010-2019 (Excel)
    2020-2025   State Population Totals: 2020-2025 (Excel)

Unlike the national data, all three of these turned out (confirmed directly, via
debug_dump_state_sources.py) to share one common "wide" table layout -- one row per
geography, one column per year -- so there's a single shared parser,
parse_state_wide_table(), in state_population_parsers.py rather than one parser per
source. See that file's docstring for the confirmed layout details and the one real
quirk found (Puerto Rico is dot-prefixed like a state in the 2020-2025 file, but not in
the other two -- handled by matching against an explicit state-name whitelist rather
than the "." prefix).

Because this was built AFTER downloading and inspecting the real files (unlike
import_population.py's first draft), it should not need the same debugging cycle -- but
run it and watch the console output the same way regardless.

Run this from the repo root with the same Python environment used for scrape_nuforc.py /
export_data.py / import_population.py:

    python import_state_population.py

It will prompt for the Postgres password the same way those scripts do, then create (or
replace) a new us_state_population_by_year table.
"""

import importlib.util
import subprocess
import sys


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
    "requests": "requests",
    "openpyxl": "openpyxl",  # reads the 2010-2019 and 2020-2025 .xlsx sources
    "xlrd": "xlrd",  # reads the legacy .xls 2000-2010 source
}
_install_missing_packages(_REQUIRED_PACKAGES)

# --- Everything below is now safe to import normally ---
import getpass  # prompt for the Postgres password without echoing it to the terminal

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from state_population_parsers import parse_state_wide_table

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nuforc-project-population-import/1.0)"
}
REQUEST_TIMEOUT = 60

# Each entry lists one or more candidate URLs to try in order, same fallback pattern
# debug_dump_state_sources.py uses -- confirmed working URLs are listed first.
SOURCES = {
    "2000-2010": [
        "https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/state/st-est00int-01.xls",
    ],
    "2010-2019": [
        "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/state/totals/nst-est2019-01.xlsx",
        "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/state/totals/NST-EST2019-01.xlsx",
    ],
    "2020-2025": [
        "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/state/totals/NST-EST2025-POP.xlsx",
        "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/state/totals/nst-est2025-pop.xlsx",
    ],
}


def download(vintage, urls):
    print(f"\nDownloading {vintage} state source:")
    last_error = None
    for url in urls:
        print(f"  {url}")
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            print(f"    Failed ({exc}) -- trying next candidate URL if any remain.")
            last_error = exc
            continue
        print(f"  Got {len(response.content):,} bytes.")
        return response.content
    raise last_error


def build_unified_table():
    print("Fetching and parsing all three state-level Census sources...")
    per_source_results = {}
    failed_vintages = {}
    for vintage, urls in SOURCES.items():
        try:
            raw_bytes = download(vintage, urls)
            engine = "xlrd" if vintage == "2000-2010" else "openpyxl"
            try:
                per_source_results[vintage] = parse_state_wide_table(raw_bytes, vintage, excel_engine=engine)
            except ImportError:
                # xlrd is in _REQUIRED_PACKAGES above and should already be installed by
                # this point -- this only fires if that auto-install itself failed (e.g.
                # no network), so surface a clear message rather than a confusing
                # stack trace deep inside pandas.
                raise RuntimeError(
                    "Reading the 2000-2010 source (.xls) requires the 'xlrd' package, which "
                    "isn't installed and couldn't be auto-installed. Try: pip install xlrd"
                )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, same reasoning as
            # import_population.py: one source's failure shouldn't discard the others.
            print(f"  FAILED to get usable data from the {vintage} source: {exc}")
            failed_vintages[vintage] = exc
            per_source_results[vintage] = {}

    if failed_vintages:
        print(
            f"\n{len(failed_vintages)} of 3 source(s) failed and will be missing from the "
            f"result: {list(failed_vintages)}. Send the error(s) above back for a fix."
        )
    if len(failed_vintages) == len(SOURCES):
        raise RuntimeError("All three sources failed -- nothing to load. See errors above.")

    # Combine in chronological vintage order, so a later vintage's value for a shared
    # year (2010 appears in both the 2000-2010 and 2010-2019 releases; 2020 appears in
    # both the 2010-2019 and 2020-2025 releases) overwrites an earlier one.
    unified = {}  # (state, year) -> population
    source_vintage_by_key = {}
    overlap_notes = 0
    for vintage in ["2000-2010", "2010-2019", "2020-2025"]:
        for state, by_year in per_source_results[vintage].items():
            for year, population in by_year.items():
                key = (state, year)
                if key in unified and unified[key] != population:
                    overlap_notes += 1
                    if overlap_notes <= 5:
                        print(
                            f"  Note: {state} {year} appears in an earlier source and {vintage} "
                            f"with different values ({unified[key]:,} vs {population:,}) -- "
                            f"keeping the {vintage} figure as the more current one."
                        )
                unified[key] = population
                source_vintage_by_key[key] = vintage
    if overlap_notes > 5:
        print(f"  ...({overlap_notes} total overlapping state-year values resolved this way.)")

    if not unified:
        raise RuntimeError("No state-year values were parsed from any source -- nothing to load. See errors above.")

    df = pd.DataFrame(
        [
            {"state": state, "year": year, "population": population, "source_vintage": source_vintage_by_key[(state, year)]}
            for (state, year), population in unified.items()
        ]
    ).sort_values(["state", "year"]).reset_index(drop=True)

    states_found = df["state"].nunique()
    year_min, year_max = df["year"].min(), df["year"].max()
    print(f"\nCombined into a single table: {states_found} states/DC, {year_min}-{year_max}.")
    for state, group in df.groupby("state"):
        missing_years = sorted(set(range(year_min, year_max + 1)) - set(group["year"]))
        if missing_years:
            print(f"  Warning: {state} is missing {len(missing_years)} year(s): {missing_years}")

    return df


def main():
    state_population_df = build_unified_table()

    # --- Postgres connection settings (matches scrape_nuforc.py / export_data.py / import_population.py) ---
    DB_USER = "postgres"
    DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
    DB_HOST = "localhost"
    DB_PORT = "5432"
    DB_NAME = "nuforc"
    TABLE_NAME = "us_state_population_by_year"

    engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    try:
        with engine.connect():
            pass
        print(f"\nConnected to Postgres database '{DB_NAME}' -- proceeding with the load.")
    except SQLAlchemyError as exc:
        print(f"Could not connect to Postgres:\n{exc}")
        print("Check DB_USER/DB_HOST/DB_PORT/DB_NAME above, and make sure you entered the correct password, then try again.")
        sys.exit(1)

    # Replaces the table wholesale on every run, same convention as scrape_nuforc.py /
    # export_data.py / import_population.py (see pipeline.html's "What's still rough"
    # note -- this project doesn't do incremental writes anywhere yet).
    state_population_df.to_sql(TABLE_NAME, engine, if_exists="replace", index=False)

    with engine.connect() as conn:
        row_count = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar()

    print(f"\nWrote {row_count} rows to '{TABLE_NAME}' (columns: state, year, population, source_vintage).")


if __name__ == "__main__":
    main()
