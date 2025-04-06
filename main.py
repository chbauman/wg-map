import argparse
from datetime import datetime
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from arcgis.geocoding import geocode
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from tqdm import tqdm
import urllib3
from webdriver_manager.chrome import ChromeDriverManager
from arcgis.gis import GIS

# Disable http warning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

base_path = Path(__file__).parent
CACHE_DIR = base_path / "cache"
CACHE_DIR.mkdir(exist_ok=True)

DriverType = webdriver.Chrome


def init_driver():
    """Initializes the web driver."""
    ChromeDriverManager().install()
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(100)
    return driver


def find_next_page_link(driver: DriverType, verbose: bool):
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
    return link


def find_all(driver: DriverType, verbose: bool = True):
    """Finds all adverts."""
    driver.get("https://www.wgzimmer.ch/wgzimmer/search/mate.html?reset=true")

    # Submit the form
    driver.execute_script("submitForm();")
    time.sleep(1)

    page_available = True
    ad_links = []

    while page_available:
        try:
            search_res_list = driver.find_element(By.ID, "search-result-list")
        except NoSuchElementException:
            if verbose:
                print("No items found")
            return []
        search_items = search_res_list.find_elements(By.CLASS_NAME, "search-mate-entry")
        if verbose:
            print(f"Found {len(search_items)} items.")
        ad_links += [
            list_el.find_elements(By.TAG_NAME, "a")[0].get_attribute("href")
            for list_el in search_items
        ]

        try:
            link = find_next_page_link(driver, verbose)
            driver.get(link)
            time.sleep(1)
        except NoSuchElementException:
            if verbose:
                print("No next page!")
            page_available = False
        except WebDriverException:
            print("fuck")

    unique_ads = list(set(ad_links))
    return unique_ads


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


def get_all_info(all_links: list[str]):
    cache_file_path = CACHE_DIR / "add_infos.json"

    cached = []
    if cache_file_path.exists():
        with open(cache_file_path, "r") as f:
            cached = json.load(f)

    link_to_info = {el["url"]: el for el in cached}

    new_ad_infos: list[dict] = []
    for link in tqdm(all_links, "Getting info for all links"):
        available_info = link_to_info.get(link)

        if available_info is not None:
            new_ad_infos.append(available_info)
        else:
            # Load
            new_info = get_info(link)
            new_ad_infos.append(new_info)

    with open(cache_file_path, "w") as f:
        json.dump(new_ad_infos, f)

    return new_ad_infos


def update(driver: DriverType):

    update_time = datetime.now()

    all_links = find_all(driver)
    get_all_info(all_links)

    # Save update time
    with open(CACHE_DIR / "last_update.txt", "w") as f:
        f.write(update_time.strftime("%m/%d/%Y, %H:%M:%S"))


def main():
    """The main function."""

    # Define parser and parse
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", help="update data", action="store_true")
    args = parser.parse_args()

    # Initialize the webdriver and GIS
    driver = init_driver()
    GIS()

    # Update data from wgzimmer.ch
    if args.update:
        update(driver)


if __name__ == "__main__":
    main()
