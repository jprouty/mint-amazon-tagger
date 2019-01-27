#!/usr/bin/env python3

# This script fetches Amazon "Order History Reports" and annotates your Mint
# transactions based on actual items in each purchase. It can handle orders
# that are split into multiple shipments/charges, and can even itemized each
# transaction for maximal control over categorization.

import argparse
import datetime
import io
import getpass
import keyring
import os
from progress.spinner import Spinner
import requests
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from seleniumrequests import Chrome
from sys import platform as _platform
import zipfile

from asyncprogress import AsyncProgress
import tagger

AMZN_REPORT_DOWNLOAD_BASE = 'AMZN Reports'
ORDER_HISTORY_REPORT_URL = "https://www.amazon.com/gp/b2b/reports"
ORDER_HISTORY_PROCESS_TIMEOUT_S = 60

def main():
    parser = argparse.ArgumentParser(
        description='Tag Mint transactions based on itemized Amazon history.')
    define_args(parser)
    tagger.define_common_args(parser)
    args = parser.parse_args()

    start_date = args.order_history_start_date
    duration = datetime.timedelta(days=args.order_history_num_days)
    end_date = datetime.datetime.now()
    # If a start date is given, adjust the end date based on num_days, ensuring
    # not to go beyond today.
    if start_date:
        if start_date + duration < end_date:
            end_date = start_date + duration
    else:
        start_date = end_date - duration

    email, password = get_email_and_pass(args.amazon_email, args.amazon_password)

    name = args.amazon_email.split('@')[0]
    report_names = ['{} {} from {:%d %b %Y} to {:%d %b %Y}'.format(name, t, start_date, end_date)
                    for t in ['Items', 'Orders', 'Refunds']]
    report_types = ['ITEMS', 'SHIPMENTS', 'REFUNDS']
    report_paths = [args.report_download_location + os.path.sep + name + '.csv'
                   for name in report_names]

    if not os.path.exists(args.report_download_location):
        os.makedirs(args.report_download_location)

    # Be lazy with getting the driver, as if no fetching is needed, then it's
    # all good.
    driver = None
    for report_name, report_type, report_path in zip(
            report_names, report_types, report_paths):
        print(report_name)
        if os.path.exists(report_path):
            # Report has already been fetched! Woot
            continue

        # Report is not here. Go get it
        if not driver:
            loginSpin = AsyncProgress(Spinner('Logging into Amazon '))
            driver = get_web_driver(args.amazon_email, args.amazon_password)
            loginSpin.finish()

        requestSpin = AsyncProgress(Spinner(
            'Requesting {} report '.format(report_type)))
        request_report(driver, report_name, report_type, start_date, end_date)
        requestSpin.finish()

        processingSpin = AsyncProgress(Spinner(
            'Waiting for {} report to be ready '.format(report_type)))
        try:
            wait_cond = EC.presence_of_element_located(
                (By.XPATH, get_report_download_link_xpath(report_name)))
            element = WebDriverWait(
                driver, ORDER_HISTORY_PROCESS_TIMEOUT_S).until(wait_cond)
            processingSpin.finish()
        except TimeoutException:
            print("Cannot find download link after a minute!")
            processingSpin.finish()
            exit(1)

        downloadSpin = AsyncProgress(Spinner(
            'Downloading {} report '.format(report_type)))
        download_report(driver, report_name, report_path)
        downloadSpin.finish()

    print('\nAll Amazon history has been fetched. Onto tagging.')
    driver.finish()

def get_email_and_pass(email, password):
    if not email:
        email = input('Amazon email: ')

    # This was causing my grief. Let's let it rest for a while.
    # if not password:
    #     password = keyring.get_password(KEYRING_SERVICE_NAME, email)

    if not password:
        password = getpass.getpass('Amazon password: ')

    if not email or not password:
        logger.error('Missing Amazon email or password.')
        exit(1)
    return email, password


CHROME_DRIVER_VERSION = 2.41
CHROME_DRIVER_BASE_URL = 'https://chromedriver.storage.googleapis.com/%s/chromedriver_%s.zip'
CHROME_ZIP_TYPES = {
    'linux': 'linux64',
    'linux2': 'linux64',
    'darwin': 'mac64',
    'win32': 'win32',
    'win64': 'win32'
}

def get_web_driver(email, password, headless=False, wait_for_sync=True,
                   session_path=None):
    zip_type = ""
    executable_path = os.getcwd() + os.path.sep + 'chromedriver'
    if _platform in ['win32', 'win64']:
        executable_path += '.exe'

    zip_type = CHROME_ZIP_TYPES.get(_platform)

    if not os.path.exists(executable_path):
        zip_file_url = CHROME_DRIVER_BASE_URL % (CHROME_DRIVER_VERSION, zip_type)
        request = requests.get(zip_file_url)

        if request.status_code != 200:
            raise RuntimeError('Error finding chromedriver at %r, status = %d' %
                               (zip_file_url, request.status_code))

        zip_file = zipfile.ZipFile(io.BytesIO(request.content))
        zip_file.extractall()
        os.chmod(executable_path, 0o755)

    chrome_options = ChromeOptions()
    if headless:
        chrome_options.add_argument('headless')
        chrome_options.add_argument('no-sandbox')
        chrome_options.add_argument('disable-dev-shm-usage')
        chrome_options.add_argument('disable-gpu')
        # chrome_options.add_argument("--window-size=1920x1080")
    if session_path is not None:
        chrome_options.add_argument("user-data-dir=%s" % session_path)

    driver = Chrome(chrome_options=chrome_options, executable_path="%s" % executable_path)

    driver.get(ORDER_HISTORY_REPORT_URL)
    driver.implicitly_wait(10)
    driver.find_element_by_id("ap_email").send_keys(email)
    driver.find_element_by_id("ap_password").send_keys(password)
    driver.find_element_by_id("signInSubmit").submit()
    driver.implicitly_wait(10)

    try:
        driver.find_element_by_id("report-confirm").submit()
    except NoSuchElementException:
        # No luck; probably 2factor auth or bad credentials
        return None

    return driver


def request_report(driver, report_name, report_type, start_date, end_date):
    try:
        # Do not request the report again if it's already available for download
        driver.find_element_by_xpath(get_report_download_link_xpath(report_name))
        return
    except NoSuchElementException:
        pass

    Select(driver.find_element_by_id(
        'report-type')).select_by_value(report_type)

    Select(driver.find_element_by_id(
        'report-month-start')).select_by_value(str(start_date.month))
    Select(driver.find_element_by_id(
        'report-day-start')).select_by_value(str(start_date.day))
    Select(driver.find_element_by_id(
        'report-year-start')).select_by_value(str(start_date.year))

    Select(driver.find_element_by_id(
        'report-month-end')).select_by_value(str(end_date.month))
    Select(driver.find_element_by_id(
        'report-day-end')).select_by_value(str(end_date.day))
    Select(driver.find_element_by_id(
        'report-year-end')).select_by_value(str(end_date.year))

    driver.find_element_by_id('report-name').send_keys(report_name)

    # Submit will not work as the input type is an image (nice Amazon)
    driver.find_element_by_id('report-confirm').click()


def get_report_download_link_xpath(report_name):
    return "//td[contains(text(), '{}')]/..//td/a[text()='Download']".format(
        report_name)


def download_report(driver, report_name, report_path):
    # 1. Find the report download link
    report_url = None
    try:
        download_link = driver.find_element_by_xpath(
            get_report_download_link_xpath(report_name))
        report_url = download_link.get_attribute('href')
    except NoSuchElementException:
        print('Could not find the download link!')
        exit(1)
    # //td[contains(text(), 'test_items')]/..//td[contains(@id, 'download')]

    # 2. Download the report to the AMZN Reports directory
    response = driver.request('GET', report_url,
                              allow_redirects=True)
    response.raise_for_status()
    with open(report_path, 'w') as fh:
        fh.write(response.text)


def define_args(parser):
    # Amazon creds:
    parser.add_argument(
        '--amazon_email', default=None,
        help=('Amazon e-mail. If not provided, you will be '
              'prompted for it.'))
    parser.add_argument(
        '--amazon_password', default=None,
        help=('Amazon password. If not provided, you will be '
              'prompted for it.'))

    # History options"
    parser.add_argument(
        '--order_history_location', type=str,
        default="AMZN Reports",
        help='Where to store the fetched Amazon "order history" reports.')
    parser.add_argument(
        '--order_history_num_days', type=int,
        default=90,
        help='How many days of order history to retrieve. Default: 90 days')
    parser.add_argument(
        '--order_history_start_date',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'),
        default=None,
        help=('If None, start_date is num_days ago from today. '
              'If given, this is the start_date, with the end date being '
              'start_date + num_days. Format: YYYY-MM-DD'))
    parser.add_argument(
        '--report_download_lorder_history_num_daysocation', type=str,
        default=AMZN_REPORT_DOWNLOAD_BASE,
        help='Where to place the downloaded reports.')


if __name__ == '__main__':
    main()
