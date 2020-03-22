import atexit
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ORDER_HISTORY_URL_VIA_SWITCH_ACCOUNT_LOGIN = (
    'https://www.amazon.com/gp/navigation/redirector.html/ref=sign-in-redirect'
    '?ie=UTF8&associationHandle=usflex&currentPageURL='
    'https%3A%2F%2Fwww.amazon.com%2Fgp%2Fyourstore%2Fhome%3Fie%3DUTF8%26'
    'ref_%3Dnav_youraccount_switchacct&pageType=&switchAccount=picker&'
    'yshURL=https%3A%2F%2Fwww.amazon.com%2Fgp%2Fb2b%2Freports')
ORDER_HISTORY_REPORT_URL = 'https://www.amazon.com/gp/b2b/reports'
ORDER_HISTORY_PROCESS_TIMEOUT_S = 60


class NoProgress:
    def next(self, i=1):
        pass

    def finish(self):
        pass


def fetch_order_history(report_download_path, start_date, end_date,
                        email=None, password=None,
                        session_path=None, headless=False,
                        progress_factory=lambda msg, max: NoProgress()):
    email = get_email(email)
    name = email.split('@')[0]

    report_shortnames = ['Items', 'Orders', 'Refunds']
    report_names = ['{} {} from {:%d %b %Y} to {:%d %b %Y}'.format(
                    name, t, start_date, end_date)
                    for t in report_shortnames]
    report_types = ['ITEMS', 'SHIPMENTS', 'REFUNDS']
    report_paths = [os.path.join(report_download_path, name + '.csv')
                    for name in report_names]

    os.makedirs(report_download_path, exist_ok=True)

    # Be lazy with getting the driver, as if no fetching is needed, then it's
    # all good.
    driver = None
    for report_shortname, report_type, report_name, report_path in zip(
            report_shortnames, report_types, report_names, report_paths):
        if os.path.exists(report_path):
            # Report has already been fetched! Woot
            continue

        # Report is not here. Go get it
        if not driver:
            login_progress = progress_factory(
                'Launching Chrome and Signing into Amazon.com to request '
                'order reports.', 0)
            driver = get_amzn_driver(email, password,
                                     headless=headless,
                                     session_path=session_path)
            login_progress.finish()

            def close_webdriver():
                if driver:
                    driver.close()

            atexit.register(close_webdriver)

        request_progress = progress_factory(
            'Requesting {} report '.format(report_shortname), 0)
        request_report(driver, report_name, report_type, start_date, end_date)
        request_progress.finish()

        processing_progress = progress_factory(
            'Waiting for {} report to be ready '.format(report_shortname), 0)
        try:
            wait_cond = EC.presence_of_element_located(
                (By.XPATH, get_report_download_link_xpath(report_name)))
            WebDriverWait(
                driver, ORDER_HISTORY_PROCESS_TIMEOUT_S).until(wait_cond)
            processing_progress.finish()
        except TimeoutException:
            processing_progress.finish()
            logger.critical("Cannot find download link after a minute!")
            exit(1)

        download_progress = progress_factory(
            'Downloading {} report '.format(report_shortname), 0)
        download_report(driver, report_name, report_path)
        download_progress.finish()

    if driver:
        closer = progress_factory(
            'Done with the Chrome window for Amazon. Closing', 0)
        driver.close()
        driver = None
        closer.finish()

    return (
        open(report_paths[0], 'r', encoding='utf-8'),
        open(report_paths[1], 'r', encoding='utf-8'),
        open(report_paths[2], 'r', encoding='utf-8'))


def get_email(email):
    if not email:
        email = input('Amazon email: ')

    if not email:
        logger.error('Empty Amazon email.')
        exit(1)

    return email


def get_password(password):
    if not password:
        password = getpass.getpass('Amazon password: ')

    if not password:
        logger.error('Empty Amazon password.')
        exit(1)

    return password


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
    home = os.path.expanduser("~")
    zip_type = ""
    executable_path = os.path.join(home, 'chromedriver')
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
        zip_file.extractall(path=home)
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

    logger.info('Logging into Amazon.com')

    driver = Chrome(options=chrome_options,
                    executable_path=executable_path)

    driver.get(ORDER_HISTORY_URL_VIA_SWITCH_ACCOUNT_LOGIN)

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

    # Go straight to the account switcher, and look for the given email.
    # If present, click on it! Otherwise, click on "Add account".
    desired_account_element = get_element_by_xpath(
        driver,
        "//div[contains(text(), '{}')]".format(email))
    if desired_account_element:
        desired_account_element.click()
        driver.implicitly_wait(2)

        # It's possible this account has already authed recently. If so, the
        # next block will be skipped and the login is complete!
        if not get_element_by_id(driver, 'report-confirm'):
            driver.find_element_by_id('ap_password').send_keys(
                get_password(password))
            driver.find_element_by_name('rememberMe').click()
            driver.find_element_by_id('signInSubmit').submit()
    else:
        # Cannot find the desired account in the switch. Log in via Add Account
        driver.find_element_by_xpath(
            '//div[text()="Add account"]').click()
        driver.implicitly_wait(2)

        driver.find_element_by_id('ap_email').send_keys(email)

        # Login flow sometimes asks just for the email, then a
        # continue button, then password.
        if get_element_by_id(driver, 'continue'):
            driver.find_element_by_id('continue').click()
            driver.implicitly_wait(2)

        driver.find_element_by_id('ap_password').send_keys(
            get_password(password))
        driver.find_element_by_name('rememberMe').click()
        driver.find_element_by_id('signInSubmit').submit()

    driver.implicitly_wait(2)

    if not get_element_by_id(driver, 'report-confirm'):
        logger.warning('Having trouble logging into Amazon. Please see the '
                       'browser and complete login within the next 5 minutes. '
                       'This script will continue automatically on success. '
                       'You may need to manually navigate to: {}'.format(
                           ORDER_HISTORY_REPORT_URL))
        if get_element_by_id(driver, 'auth-mfa-otpcode'):
            logger.warning('Hint: Looks like an auth challenge! Maybe check '
                           'your email')
    try:
        wait_cond = EC.presence_of_element_located((By.ID, 'report-confirm'))
        WebDriverWait(driver, 60 * 5).until(wait_cond)
    except TimeoutException:
        logger.critical('Cannot complete login!')
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
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write(response.text)
