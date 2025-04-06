import argparse
import itertools
from datetime import datetime
import json
import os
import pickle
import re
import time

import requests
from pathlib import Path
from bs4 import BeautifulSoup

from arcgis.geocoding import geocode
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import urllib3
from webdriver_manager.chrome import ChromeDriverManager
from emeki.util import create_dir
from arcgis.gis import GIS
from selenium.webdriver.support.ui import Select

# Disable http warning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

base_path = Path(__file__).parent
CACHE_DIR = base_path / "cache"
links_cache_dir = CACHE_DIR / "advert_links"
info_cache_dir = CACHE_DIR / "advert_info"
create_dir(CACHE_DIR)
create_dir(links_cache_dir)
create_dir(info_cache_dir)

DriverType = webdriver.Chrome


def cache_decorator(cache_dir: str, f_name: str):
    f_path = os.path.join(cache_dir, f_name)

    def cache_inner_decorator(fun):
        def new_fun(*args, **kwargs):
            if os.path.isfile(f_path):
                return pickle.load(open(f_path, "rb"))
            else:
                res = fun(*args, **kwargs)
                pickle.dump(res, open(f_path, "wb"))
                return res

        return new_fun

    return cache_inner_decorator


def init_driver():
    """Initializes the web driver."""
    ChromeDriverManager().install()
    chrome_options = Options()
    # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(100)
    return driver


@cache_decorator(CACHE_DIR, "states.pkl")
def find_states(driver: DriverType):
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html")
    select_element = driver.find_element(By.ID, "selector-state")

    options = select_element.find_elements(By.TAG_NAME, "option")
    names = []
    values = []
    for opt in options:
        name = opt.text.strip()
        if "alles durchsuchen" in name.lower():
            continue
        names.append(name)
        values.append(opt.get_attribute("value"))

    print(names)
    return values, names


def find_all_in(state: str, driver: DriverType, verbose: bool = True):
    """Finds all adverts in a specific region."""
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html?reset=true")
    select_el = driver.find_element(By.ID, "selector-state")

    # Create a Select object to interact with the <select> element
    select = Select(select_el)
    select.select_by_value(state)
    time.sleep(0.3)

    found = True
    if not found:
        raise ValueError("Region not found!")

    # Submit the form
    driver.execute_script("submitForm();")
    time.sleep(1)

    page_available = True
    all_items = []

    while page_available:
        try:
            search_res_list = driver.find_element(By.ID, "search-result-list")
        except NoSuchElementException:
            if verbose:
                print(f"No items found in {state}")
            return []
        search_items = search_res_list.find_elements(By.CLASS_NAME, "search-mate-entry")
        if verbose:
            print(f"Found {len(search_items)} items in {state}.")
        all_items += [
            list_el.find_elements(By.TAG_NAME, "a")[0].get_attribute("href")
            for list_el in search_items
        ]

        try:
            nav_el = driver.find_element(By.CLASS_NAME, "result-navigation")
            skip_el = nav_el.find_element(By.CLASS_NAME, "skip")
            next_link = skip_el.find_element(By.CLASS_NAME, "next")
            span = skip_el.find_element(By.TAG_NAME, "span")
            
            # Use regular expression to extract x and y
            match = re.search(r"(\d+)/(\d+)", span.text)

            if match:
                x = int(match.group(1))  # x value (current page)
                y = int(match.group(2))  # y value (total pages)
                if x / y == 1:
                    raise NoSuchElementException()
            else:
                raise WebDriverException()

            link = next_link.get_attribute("href")
            if verbose:
                print(span.text)
            driver.get(link)
            time.sleep(1)
        except NoSuchElementException:
            if verbose:
                print("No next page!")
            page_available = False
        except WebDriverException:
            print("fuck")

    return all_items


def find_all_cached(s_val, driver):
    """Uses caching to find all adverts."""

    @cache_decorator(links_cache_dir, s_val)
    def cached_find_all_helper():
        return find_all_in(s_val, driver)

    pages = cached_find_all_helper()
    return pages


def get_info(ad_url: str):
    """Get content of url and extract info from HTML."""
    dic = {}
    try:
        r = requests.get(ad_url, verify=False)
        soup = BeautifulSoup(r.text, "lxml")
        data_price = soup.select_one('div[class^="wrap col-wrap date-cost"]')
        p = str(data_price).split("Monat</strong>")[1].split("</p>")[0].strip()
        assert "Agentur" not in p
        address = soup.select_one('div[class^="wrap col-wrap adress-region"]')
        ad = str(address).split("Adresse</strong>")[1].split("</p>")[0].strip()
        loc = str(address).split("Ort</strong>")[1].split("</p>")[0].strip()
        ad_coords = geocode(f"{ad}, {loc}")[0]["location"]
        dic = {
            "url": ad_url,
            "loc": loc,
            "address": ad,
            "price": p,
            "coords": ad_coords,
        }
    except IndexError as e:
        print(f"Something fucked up: {e}")
    return dic


def cached_get_info(pages, s_val):
    @cache_decorator(info_cache_dir, s_val)
    def cached_get_info_helper():
        return [get_info(u) for u in pages]

    return cached_get_info_helper()


def save_to_json(adverts):
    """Saves the adverts to a json file `points.json`."""
    save_path = os.path.join(base_path, "points.json")

    modified_list = [
        {**{"id": ct, "longitude": a["coords"]["x"], "latitude": a["coords"]["y"]}, **a}
        for ct, a in enumerate(adverts)
        if a
    ]
    save_dict = {"places": modified_list}

    with open(save_path, "w") as f:
        json.dump(save_dict, f, indent=4)


def save_all(driver):
    state_values, _ = find_states(driver)
    ads = [cached_get_info([], s_val) for s_val in state_values]

    save_to_json(list(itertools.chain.from_iterable(ads)))


def init(driver):
    # Find all available regions
    state_values, state_names = find_states(driver)

    # Find pages
    adverts = []
    for s_val, s_name in zip(state_values, state_names):
        pages = find_all_cached(s_val, driver)

        adverts += cached_get_info(pages, s_val)

    save_to_json(adverts)
    print("Initialized!")


def update(driver: DriverType, force: bool = False):

    update_time = datetime.now()

    state_values, state_names = find_states(driver)
    state_values, state_names = reversed(state_values), reversed(state_names)

    for s_val, s_name in zip(state_values, state_names):
        print(f"\nProcessing {s_name}")
        link_path = links_cache_dir / s_val
        if link_path.exists():
            last_changed = datetime.fromtimestamp(os.path.getmtime(link_path))
            now = datetime.now()
            d1_ts = time.mktime(last_changed.timetuple())
            d2_ts = time.mktime(now.timetuple())
            n_min_since_last_update = int(d2_ts - d1_ts) / 60
            if not force and n_min_since_last_update < 60:
                continue

        pages = find_all_cached(s_val, driver)
        info_dicts = cached_get_info(pages, s_val)

        # Look for new ones
        new_pages = find_all_in(s_val, driver, verbose=False)
        new_info_dicts = [get_info(p) for p in new_pages if p not in pages]
        n_new = len(new_info_dicts)
        if n_new > 0:
            print(f"Found {n_new} new ads in {s_name}")

        # Save new pages
        pickle.dump(new_pages, open(link_path, "wb"))

        # Look for expired ones
        remove_p = [p for p in pages if p not in new_pages]
        clean_info_dicts = [d for d in info_dicts if d and d["url"] not in remove_p]
        n_remove = len(remove_p)
        if n_remove > 0:
            print(f"{n_remove} ads removed in {s_name}")

        # Save new info dicts
        updated_dicts = clean_info_dicts + new_info_dicts
        f_path = os.path.join(info_cache_dir, s_val)
        pickle.dump(updated_dicts, open(f_path, "wb"))

    # Save json
    save_all(driver)

    # Save update time
    with open(CACHE_DIR / "last_update.txt", "w") as f:
        f.write(update_time.strftime("%m/%d/%Y, %H:%M:%S"))


def main():
    """The main function."""

    # Define parser and parse
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", help="update data", action="store_true")
    parser.add_argument("--init", help="initialize data", action="store_true")
    parser.add_argument("--force", help="force update", action="store_true")
    args = parser.parse_args()

    # Initialize the webdriver and GIS
    driver = init_driver()
    GIS()

    if args.init:
        init(driver)

    # Update data from wgzimmer.ch
    if args.update:
        update(driver, args.force)



if __name__ == "__main__":
    main()
