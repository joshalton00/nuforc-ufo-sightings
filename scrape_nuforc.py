"""scrape_nuforc.py

Scrapes the full NUFORC sighting index (https://nuforc.org/subndx/?id=all) and writes
the results into a Postgres table.

This script self-installs its own third-party dependencies on first run, and patches in
a Python 3.12+ compatibility shim that one of those dependencies needs. Both of those
have to happen *before* the normal imports below can succeed, which is why they're
handled by the two helper functions immediately following this docstring rather than as
plain top-of-file imports.
"""

import functools
import importlib.util
import re
import subprocess
import sys
import types


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


def _shim_distutils_for_undetected_chromedriver():
    """undetected_chromedriver still does `from distutils.version import LooseVersion`
    internally, but Python 3.12+ removed distutils from the standard library entirely.
    Register a small hand-rolled stand-in -- matching the bit of LooseVersion's interface
    undetected_chromedriver actually relies on (a `.version` list of integer components;
    packaging.version.Version doesn't expose that same attribute) -- so the import
    succeeds regardless of Python version. No-op if the real distutils is available."""
    if "distutils" in sys.modules:
        return
    try:
        import distutils 
        return
    except ModuleNotFoundError:
        pass

    @functools.total_ordering
    class LooseVersion:
        _component_re = re.compile(r"(\d+|[a-zA-Z]+|\.)")

        def __init__(self, vstring):
            self.vstring = vstring
            parts = [c for c in self._component_re.split(vstring) if c and c != "."]
            self.version = [int(p) if p.isdigit() else p for p in parts]

        def __str__(self):
            return self.vstring

        def __repr__(self):
            return f"LooseVersion('{self.vstring}')"

        def __eq__(self, other):
            other = other if isinstance(other, LooseVersion) else LooseVersion(str(other))
            return self.version == other.version

        def __lt__(self, other):
            other = other if isinstance(other, LooseVersion) else LooseVersion(str(other))
            return self.version < other.version

    distutils_module = types.ModuleType("distutils")
    version_module = types.ModuleType("distutils.version")
    version_module.LooseVersion = LooseVersion
    distutils_module.version = version_module
    sys.modules["distutils"] = distutils_module
    sys.modules["distutils.version"] = version_module


_REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "selenium": "selenium",
    "lxml": "lxml",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    "undetected_chromedriver": "undetected-chromedriver",
}
_install_missing_packages(_REQUIRED_PACKAGES)
_shim_distutils_for_undetected_chromedriver()

# --- Everything below is now safe to import normally ---
import getpass  # prompt for the Postgres password without echoing it to the terminal
import os  # filesystem paths
import shutil  # clearing undetected_chromedriver's cached driver binary later on
from io import StringIO  # newer pandas needs literal HTML wrapped in StringIO, not a raw string

import pandas as pd  # table dataframes for the scraped data
import undetected_chromedriver as uc  # Chrome driver that avoids Cloudflare bot detection
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from sqlalchemy import create_engine  # write the scraped dataframe to Postgres
from sqlalchemy.exc import SQLAlchemyError  # fail fast with a clear message if Postgres isn't reachable

from pagination import is_last_page

# --- Postgres connection settings ---
# Set up and verify the database connection FIRST, before opening the browser and running
# the (slow, multi-page) scrape loop below -- a bad password/host/database name should
# fail here in a couple of seconds, not after several minutes of scraping.
# The "nuforc" database must already exist (create it once in pgAdmin: right-click
# Databases > Create > Database...). The target table is created/replaced automatically
# further down based on the scraped dataframe's columns, so no manual table setup is needed.
DB_USER = "postgres"
DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "nuforc"
TABLE_NAME = "nuforc_ufo_data"

engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

try:
    with engine.connect():
        pass
    print(f"Connected to Postgres database \'{DB_NAME}\' -- proceeding with the scrape.")
except SQLAlchemyError as exc:
    print(f"Could not connect to Postgres before starting the scrape:\n{exc}")
    print("Check DB_USER/DB_HOST/DB_PORT/DB_NAME above, and make sure you entered the correct password, then try again.")
    sys.exit(1)



# web scraping loop

# opens google chrome using headless browser Selenium

nuforc_url = r"https://nuforc.org/subndx/?id=all"

# --- Chrome / Cloudflare setup ---
# Persistent Chrome profile so that once you manually clear a Cloudflare check, the
# browser stays "trusted" across future runs instead of re-challenging every time.
CHROME_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")

chrome_options = uc.ChromeOptions()
# "eager" hands control back once the DOM is ready instead of waiting for the full page
# "load" event. If Cloudflare's challenge keeps reloading/redirecting, "load" may never
# fire, and driver.get() would otherwise hang indefinitely with the default strategy.
chrome_options.page_load_strategy = "eager"

def _detect_chrome_major_version():
    """Find the installed Chrome browser's major version so undetected_chromedriver
    downloads/patches a chromedriver build that actually matches it, instead of
    whatever "latest" happens to resolve to (which can be a version ahead of what's
    actually installed -- the cause of the "only supports Chrome version X" error).
    Reads the registry first -- Chrome keeps this updated regardless of install path --
    then falls back to asking chrome.exe directly at a couple of common locations."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon")
        version, _ = winreg.QueryValueEx(key, "version")
        return int(version.split(".")[0])
    except (OSError, ValueError):
        pass

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for chrome_path in candidates:
        if os.path.exists(chrome_path):
            try:
                output = subprocess.check_output([chrome_path, "--version"], text=True)
                match = re.search(r"(\d+)\.", output)
                if match:
                    return int(match.group(1))
            except (subprocess.CalledProcessError, OSError):
                continue
    return None  # fall back to undetected_chromedriver's own auto-detection

# undetected_chromedriver caches a patched chromedriver binary on disk and doesn't
# always re-fetch it just because version_main changed, which is how a mismatched
# driver from an earlier run can keep getting reused. Clear the cache before every
# launch so it's always forced to download a build matching the detected version.
# (Safe to remove this block later, once versions have settled, to skip the re-download.)
_UC_CACHE_DIR = os.path.normpath(os.path.expanduser("~/appdata/roaming/undetected_chromedriver"))
if os.path.isdir(_UC_CACHE_DIR):
    shutil.rmtree(_UC_CACHE_DIR, ignore_errors=True)

driver = uc.Chrome(
    options=chrome_options,
    user_data_dir=CHROME_PROFILE_DIR,
    version_main=_detect_chrome_major_version(),
)

# opens nuforc url
driver.get(url=nuforc_url)
driver.maximize_window()

# Cloudflare may show a "Verify you are human" checkbox here. The browser window is
# visible (not headless), so click it by hand if it appears -- this wait gives you time to.
print("If a Cloudflare verification checkbox appears in the browser window, please click it now...")
try:
    WebDriverWait(driver, 180).until(
        expected_conditions.visibility_of_element_located((By.ID, "table_1"))
    )
    print("Table loaded, starting scrape.")
except TimeoutException:
    print("Table still not visible after 180 seconds -- the page may still be blocked.")



# sets empty datafram
df = pd.DataFrame()

# tracks whether the loop below finished because it reached the real last page (True) or
# bailed out early due to a timeout (False) -- used to flag a possibly-incomplete write
scrape_completed = False

while True:
    try:
        # Grabs table element and pulls in data as raw html
        current_table_data = WebDriverWait(driver, 20).until(
            expected_conditions.visibility_of_element_located(
            (By.ID, "table_1")
            )).get_attribute("outerHTML")

        # Appends NUFORC data to dataframe
        # EDIT: used concat instead of append as append will be deprecated
        df = pd.concat([df, pd.read_html(StringIO(current_table_data))[0]], ignore_index=True)

        # finds next button
        next_button = WebDriverWait(driver, 20).until(
            expected_conditions.presence_of_element_located((By.ID, "table_1_next")))

        if is_last_page(next_button.get_attribute("class")):
            print("loop finished")
            scrape_completed = True
            break

        # remember the current page's first row so we can tell once the new page has
        # actually rendered, instead of guessing with a flat sleep on every single page
        first_row = driver.find_element(By.CSS_SELECTOR, "#table_1 tbody tr")

        # clicks the button. A plain .click() can raise ElementClickInterceptedException
        # if the pagination bar happens to visually overlap a table cell at the exact pixel
        # Selenium targets -- scrolling the button into view first, then clicking it via
        # JavaScript (which is dispatched straight to the element, bypassing that visual
        # hit-test) sidesteps the issue entirely.
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        driver.execute_script("arguments[0].click();", next_button)

        # Staleness alone only proves the OLD row got detached -- DataTables can briefly
        # clear the table into a "processing" state before the new page's data actually
        # arrives, so staleness can fire before the new content is really there. Wait for
        # a real first row to show back up too, and retry that second wait a couple of
        # times before giving up, since one slow page shouldn't abort the whole scrape.
        WebDriverWait(driver, 20).until(expected_conditions.staleness_of(first_row))
        for attempt in range(1, 4):
            try:
                WebDriverWait(driver, 20).until(
                    expected_conditions.presence_of_element_located((By.CSS_SELECTOR, "#table_1 tbody tr"))
                )
                break
            except TimeoutException:
                if attempt == 3:
                    raise
                print(f"New page hadn't rendered yet (attempt {attempt}/3), still waiting...")

        driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.CONTROL + Keys.HOME)

    except TimeoutException:
        print("Timed out waiting for a page to load -- stopping early. The data scraped "
              "so far will still be written below, but it is likely INCOMPLETE (not the "
              "full NUFORC index).")
        break

driver.quit()

if not scrape_completed:
    print(f"WARNING: scrape stopped early -- only {len(df)} rows were collected, and this "
          f"is likely a partial dataset, not the full NUFORC index.")

# "replace" drops and recreates the table each run, since the scrape always pulls the
# full index from page 1 (matches current no-dedup behavior). Switch to if_exists="append"
# later if upversion_csv_function.py gets wired in for incremental/dedup updates instead.
df.to_sql(TABLE_NAME, engine, if_exists="replace", index=False, method="multi", chunksize=1000)
print(f"Wrote {len(df)} rows to {DB_NAME}.{TABLE_NAME}" + ("" if scrape_completed else " (INCOMPLETE)"))