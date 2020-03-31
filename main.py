import json
import os
import pickle
import time
from typing import List

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
CACHE_DIR = os.path.join(base_path, ".cache")
create_dir(CACHE_DIR)


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
    driver = webdriver.Chrome(ChromeDriverManager().install())
    driver.set_page_load_timeout(100)
    return driver


@cache_decorator(CACHE_DIR, "states.pkl")
def find_states(driver):
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html")
    el = driver.find_element_by_name("state")
    print(el)
    states = el.get_attribute('innerHTML').split("> <")
    states = [s.split("option")[1] for s in states]
    state_names = [s.split(">")[1].split("<")[0] for s in states]
    state_values = [s.split("value=")[1].split(">")[0][1:-1] for s in states]
    return state_values, state_names


def find_all_in(state: str, driver):
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
            print(f"No items found in {state}")
            return []
        search_items = search_res_list.find_elements_by_class_name('search-mate-entry')
        print(f"Found {len(search_items)} items in {state}.")
        all_items += [list_el.find_elements_by_tag_name("a")[1].get_attribute("href") for list_el in search_items]

        try:
            next_page = driver.find_element_by_id("gtagSearchresultNextPage")
            print(f"Found next page: {next_page.get_attribute('innerHTML')}")
            driver.execute_script("nextPage();")
            time.sleep(1)
        except NoSuchElementException:
            print(f"No next page!")
            page_available = False

    return all_items


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
    print(dic)
    return dic


def get_region_info(url_list: List):
    return [get_info(u) for u in url_list]


def save_to_json(adverts):
    save_path = os.path.join(CACHE_DIR, "points.json")

    modified_list = [
        {**{"id": ct, "longitude": a["coords"]["x"], "latitude": a["coords"]["y"]}, **a}
        for ct, a in enumerate(adverts) if a
    ]
    save_dict = {"places": modified_list}

    with open(save_path, 'w') as f:
        json.dump(save_dict, f, indent=4)


def main():
    # Initialize the webdriver
    driver = init_driver()
    gis = GIS()

    # Find all available regions
    state_values, state_names = find_states(driver)

    # Find pages
    links_cache_dir = os.path.join(CACHE_DIR, "advert_links")
    info_cache_dir = os.path.join(CACHE_DIR, "advert_info")
    create_dir(links_cache_dir)
    create_dir(info_cache_dir)
    pages = []
    adverts = []
    for s_val, s_name in zip(state_values, state_names):

        @cache_decorator(links_cache_dir, s_val)
        def cached_find_all():
            return find_all_in(s_val, driver)

        pages = cached_find_all()

        @cache_decorator(info_cache_dir, s_val)
        def cached_get_info():
            return get_region_info(pages)

        adverts += cached_get_info()

    save_to_json(adverts)

    time.sleep(100)
    pass


if __name__ == "__main__":
    main()
