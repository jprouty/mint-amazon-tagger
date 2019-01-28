import getpass
import io
import logging
import os
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

from progress.spinner import Spinner

from asyncprogress import AsyncProgress

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


ORDER_HISTORY_REPORT_URL = "https://www.amazon.com/gp/b2b/reports"
ORDER_HISTORY_PROCESS_TIMEOUT_S = 60


def fetch_order_history(start_date, end_date, args):
    email, password = get_email_and_pass(
        args.amazon_email, args.amazon_password)

    name = args.amazon_email.split('@')[0]
    report_names = ['{} {} from {:%d %b %Y} to {:%d %b %Y}'.format(
                        name, t, start_date, end_date)
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
        if os.path.exists(report_path):
            # Report has already been fetched! Woot
            continue

        # Report is not here. Go get it
        if not driver:
            loginSpin = AsyncProgress(Spinner('Logging into Amazon '))
            driver = get_amzn_driver(args.amazon_email, args.amazon_password,
                                     headless=args.headless,
                                     session_path=args.session_path)
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
            WebDriverWait(
                driver, ORDER_HISTORY_PROCESS_TIMEOUT_S).until(wait_cond)
            processingSpin.finish()
        except TimeoutException:
            logger.critical("Cannot find download link after a minute!")
            processingSpin.finish()
            exit(1)

        downloadSpin = AsyncProgress(Spinner(
            'Downloading {} report '.format(report_type)))
        download_report(driver, report_name, report_path)
        downloadSpin.finish()

    logger.info('\nAll Amazon history has been fetched. Onto tagging.')
    if driver:
        driver.close()

    return (
        open(report_paths[0], 'r'),
        open(report_paths[1], 'r'),
        open(report_paths[2], 'r'))


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
CHROME_DRIVER_BASE_URL = ('https://chromedriver.storage.googleapis.com/'
                          '{}/chromedriver_{}.zip')
CHROME_ZIP_TYPES = {
    'linux': 'linux64',
    'linux2': 'linux64',
    'darwin': 'mac64',
    'win32': 'win32',
    'win64': 'win32'
}


def get_amzn_driver(email, password, headless=False, session_path=None):
    zip_type = ""
    executable_path = os.getcwd() + os.path.sep + 'chromedriver'
    if _platform in ['win32', 'win64']:
        executable_path += '.exe'

    zip_type = CHROME_ZIP_TYPES.get(_platform)

    if not os.path.exists(executable_path):
        zip_file_url = CHROME_DRIVER_BASE_URL.format(
            CHROME_DRIVER_VERSION, zip_type)
        request = requests.get(zip_file_url)

        if request.status_code != 200:
            raise RuntimeError(
                'Error finding chromedriver at {}, status = {}'.format(
                    zip_file_url, request.status_code))

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
        chrome_options.add_argument("user-data-dir=" + session_path)

    driver = Chrome(chrome_options=chrome_options,
                    executable_path=executable_path)

    driver.get(ORDER_HISTORY_REPORT_URL)

    driver.implicitly_wait(2)

    def get_element_by_id(driver, id):
        try:
            return driver.find_element_by_id(id)
        except NoSuchElementException:
            pass
        return None

    def get_element_by_xpath(driver, xpath):
        try:
            return driver.find_element_by_xpath(xpath)
        except NoSuchElementException:
            pass
        return None

    # Full login on first visit: no user history, just do it
    if get_element_by_id(driver, 'ap_email'):
        driver.find_element_by_id('ap_email').send_keys(email)
        driver.find_element_by_id('ap_password').send_keys(password)
        driver.find_element_by_name('rememberMe').click()
        driver.find_element_by_id('signInSubmit').submit()
    else:
        current_account = get_element_by_xpath(
            driver,
            "//div[contains(text(), '{}')]".format(email))
        # If the current account was previously logged in, just enter the
        # password.
        if current_account:
            driver.find_element_by_id('ap_password').send_keys(password)
            driver.find_element_by_name('rememberMe').click()
            driver.find_element_by_id('signInSubmit').submit()
        # Go to the account switcher:
        else:
            driver.find_element_by_id('ap_switch_account_link').click()
            driver.implicitly_wait(2)

            current_account = get_element_by_xpath(
                driver,
                "//div[contains(text(), '{}')]".format(email))
            if current_account:
                current_account.click()
                driver.implicitly_wait(2)

                driver.find_element_by_id('ap_password').send_keys(password)
                driver.find_element_by_name('rememberMe').click()
                driver.find_element_by_id('signInSubmit').submit()
            else:
                driver.find_element_by_xpath(
                    '//div[text()="Add account"]').click()
                driver.implicitly_wait(2)

                driver.find_element_by_id('ap_email').send_keys(email)
                driver.find_element_by_id('ap_password').send_keys(password)
                driver.find_element_by_name('rememberMe').click()
                driver.find_element_by_id('signInSubmit').submit()

    driver.implicitly_wait(2)

    if get_element_by_id(driver, 'auth-mfa-otpcode'):
        logger.warning('Please answer the OTP challenge in browser! You have '
                       '5 min')
        try:
            wait_cond = EC.presence_of_element_located(
                (By.ID, 'report-confirm'))
            WebDriverWait(driver, 60*5).until(wait_cond)
        except TimeoutException:
            logger.error('Cannot get past 2factor auth!')
            exit(1)
    try:
        driver.find_element_by_id('report-confirm').submit()
    except NoSuchElementException:
        # No luck; probably 2factor auth or bad credentials
        logger.critical('Had trouble logging in!')
        exit(1)

    return driver


def request_report(driver, report_name, report_type, start_date, end_date):
    try:
        # Do not request the report again if it's already available for
        # download.
        driver.find_element_by_xpath(
            get_report_download_link_xpath(report_name))
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
        logger.critical('Could not find the download link!')
        exit(1)

    # 2. Download the report to the AMZN Reports directory
    response = driver.request('GET', report_url,
                              allow_redirects=True)
    response.raise_for_status()
    with open(report_path, 'w') as fh:
        fh.write(response.text)
