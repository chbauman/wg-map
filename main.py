import itertools
from datetime import datetime
import json
import os
import pickle
import time
from typing import List, Optional

import requests
from pathlib import Path
from bs4 import BeautifulSoup

from arcgis.geocoding import geocode
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from emeki.util import create_dir
from arcgis.gis import GIS

base_path = Path(__file__).parent
CACHE_DIR = os.path.join(base_path, "cache")
links_cache_dir = os.path.join(CACHE_DIR, "advert_links")
info_cache_dir = os.path.join(CACHE_DIR, "advert_info")
create_dir(CACHE_DIR)
create_dir(links_cache_dir)
create_dir(info_cache_dir)


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
    driver = webdriver.Chrome(ChromeDriverManager().install())
    driver.set_page_load_timeout(100)
    return driver


@cache_decorator(CACHE_DIR, "states.pkl")
def find_states(driver: Optional):
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html")
    el = driver.find_element_by_name("state")
    print(el)
    states = el.get_attribute('innerHTML').split("> <")
    states = [s.split("option")[1] for s in states]
    state_names = [s.split(">")[1].split("<")[0] for s in states]
    state_values = [s.split("value=")[1].split(">")[0][1:-1] for s in states]
    return state_values, state_names


def find_all_in(state: str, driver, verbose: bool = True):
    """Finds all adverts in a specific region."""
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html")
    select_el = driver.find_element_by_id('selector-state')

    found = False
    for option in select_el.find_elements_by_tag_name('option'):
        if option.get_attribute("value") == state:
            found = True
            option.click()  # select() in earlier versions of webdriver
            time.sleep(0.1)

    if not found:
        raise ValueError("Region not found!")

    driver.execute_script("submitForm();")
    time.sleep(1)

    page_available = True
    all_items = []

    while page_available:
        try:
            search_res_list = driver.find_element_by_id('search-result-list')
        except NoSuchElementException:
            if verbose:
                print(f"No items found in {state}")
            return []
        search_items = search_res_list.find_elements_by_class_name('search-mate-entry')
        if verbose:
            print(f"Found {len(search_items)} items in {state}.")
        all_items += [list_el.find_elements_by_tag_name("a")[1].get_attribute("href") for list_el in search_items]

        try:
            next_page = driver.find_element_by_id("gtagSearchresultNextPage")
            if verbose:
                print(f"Found next page: {next_page.get_attribute('innerHTML')}")
            driver.execute_script("nextPage();")
            time.sleep(1)
        except NoSuchElementException:
            if verbose:
                print(f"No next page!")
            page_available = False

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
        r = requests.get(ad_url)
        soup = BeautifulSoup(r.text, 'lxml')
        data_price = soup.select_one('div[class^="wrap col-wrap date-cost"]')
        p = str(data_price).split("</strong>")[-1].split("</p>")[0].strip()
        address = soup.select_one('div[class^="wrap col-wrap adress-region"]')
        ad = str(address).split("Adresse</strong>")[1].split("</p>")[0].strip()
        loc = str(address).split("Ort</strong>")[1].split("</p>")[0].strip()
        ad_coords = geocode(f"{ad}, {loc}")[0]["location"]
        dic = {"url": ad_url, "loc": loc, "address": ad, "price": p, "coords": ad_coords}
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
        for ct, a in enumerate(adverts) if a
    ]
    save_dict = {"places": modified_list}

    with open(save_path, 'w') as f:
        json.dump(save_dict, f, indent=4)


def save_all(driver):
    state_values, state_names = find_states(driver)
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


def update(driver):

    update_time = datetime.now()

    state_values, state_names = find_states(driver)

    for s_val, s_name in zip(state_values, state_names):
        print(f"\nProcessing {s_name}")
        link_path = os.path.join(links_cache_dir, s_val)
        last_changed = datetime.fromtimestamp(os.path.getmtime(link_path))
        now = datetime.now()
        d1_ts = time.mktime(last_changed.timetuple())
        d2_ts = time.mktime(now.timetuple())
        n_min_since_last_update = int(d2_ts - d1_ts) / 60
        if n_min_since_last_update < 60:
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
    with open(os.path.join(CACHE_DIR, "last_update.txt"), "w") as f:
        f.write(update_time.strftime("%m/%d/%Y, %H:%M:%S"))


def main():
    """The main function."""

    # Initialize the webdriver and GIS
    driver = init_driver()
    GIS()

    update(driver)

    init(driver)
    pass


if __name__ == "__main__":
    main()
