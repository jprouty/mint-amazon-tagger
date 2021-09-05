import io
import logging
import os
import re
import requests
import subprocess
from sys import platform
import zipfile

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver import ChromeOptions
from seleniumrequests import Chrome

logger = logging.getLogger(__name__)


def get_webdriver(headless=False, session_path=None):
    chrome_options = ChromeOptions()
    if headless:
        chrome_options.add_argument('headless')
        chrome_options.add_argument('no-sandbox')
        chrome_options.add_argument('disable-dev-shm-usage')
        chrome_options.add_argument('disable-gpu')
    if session_path is not None:
        chrome_options.add_argument("user-data-dir=" + session_path)
    home_dir = os.path.expanduser("~")
    return Chrome(options=chrome_options,
                  executable_path=get_stable_chrome_driver(home_dir))


def is_visible(element):
    return element and element.is_displayed()


def get_element_by_id(driver, id):
    try:
        return driver.find_element_by_id(id)
    except NoSuchElementException:
        pass
    return None


def get_element_by_name(driver, name):
    try:
        return driver.find_element_by_name(name)
    except NoSuchElementException:
        pass
    return None


def get_element_by_xpath(driver, xpath):
    try:
        return driver.find_element_by_xpath(xpath)
    except NoSuchElementException:
        pass
    return None


def get_element_by_link_text(driver, link_text):
    try:
        return driver.find_element_by_link_text(link_text)
    except NoSuchElementException:
        pass
    return None


def get_elements_by_class_name(driver, class_name):
    try:
        return driver.find_elements_by_class_name(class_name)
    except NoSuchElementException:
        pass
    return None


CHROME_DRIVER_BASE_URL = 'https://chromedriver.storage.googleapis.com/'
CHROME_DRIVER_DOWNLOAD_PATH = '{version}/chromedriver_{arch}.zip'
CHROME_DRIVER_LATEST_RELEASE = 'LATEST_RELEASE'
CHROME_ZIP_TYPES = {
    'linux': 'linux64',
    'linux2': 'linux64',
    'darwin': 'mac64',
    'win32': 'win32',
    'win64': 'win32'
}
version_pattern = re.compile(
    "(?P<version>(?P<major>\\d+)\\.(?P<minor>\\d+)\\."
    "(?P<build>\\d+)\\.(?P<patch>\\d+))")


def get_chrome_driver_url(version, arch):
    return CHROME_DRIVER_BASE_URL + CHROME_DRIVER_DOWNLOAD_PATH.format(
        version=version, arch=CHROME_ZIP_TYPES.get(arch))


def get_chrome_driver_major_version_from_executable(local_executable_path):
    # Note; --version works on windows as well.
    # check_output fails if running from a thread without a console on win10.
    # To protect against this use explicit pipes for STDIN/STDERR.
    # See: https://github.com/pyinstaller/pyinstaller/issues/3392
    with open(os.devnull, 'wb') as devnull:
        version = subprocess.check_output(
            [local_executable_path, '--version'],
            stderr=devnull,
            stdin=devnull)
        version_match = version_pattern.search(version.decode())
        if not version_match:
            return None
        return version_match.groupdict()['major']


def get_latest_chrome_driver_version():
    """Returns the version of the latest stable chromedriver release."""
    latest_url = CHROME_DRIVER_BASE_URL + CHROME_DRIVER_LATEST_RELEASE
    latest_request = requests.get(latest_url)

    if latest_request.status_code != 200:
        raise RuntimeError(
            'Error finding the latest chromedriver at {}, status = {}'.format(
                latest_url, latest_request.status_code))
    return latest_request.text


def get_stable_chrome_driver(download_directory=os.getcwd()):
    chromedriver_name = 'chromedriver'
    if platform in ['win32', 'win64']:
        chromedriver_name += '.exe'

    local_executable_path = os.path.join(download_directory, chromedriver_name)

    latest_chrome_driver_version = get_latest_chrome_driver_version()
    version_match = version_pattern.match(latest_chrome_driver_version)
    latest_major_version = None
    if not version_match:
        logger.error("Cannot parse latest chrome driver string: {}".format(
            latest_chrome_driver_version))
    else:
        latest_major_version = version_match.groupdict()['major']
    if os.path.exists(local_executable_path):
        major_version = get_chrome_driver_major_version_from_executable(
            local_executable_path)
        if major_version == latest_major_version or not latest_major_version:
            # Use the existing chrome driver, as it's already the latest
            # version or the latest version cannot be determined at the moment.
            return local_executable_path
        logger.info('Removing old version {} of Chromedriver'.format(
            major_version))
        os.remove(local_executable_path)

    if not latest_chrome_driver_version:
        logger.critical(
            'No local chrome driver found and cannot parse the latest chrome '
            'driver on the internet. Please double check your internet '
            'connection, then ask for assistance on the github project.')
        return None
    logger.info('Downloading version {} of Chromedriver'.format(
        latest_chrome_driver_version))
    zip_file_url = get_chrome_driver_url(
        latest_chrome_driver_version, platform)
    request = requests.get(zip_file_url)

    if request.status_code != 200:
        raise RuntimeError(
            'Error finding chromedriver at {}, status = {}'.format(
                zip_file_url, request.status_code))

    zip_file = zipfile.ZipFile(io.BytesIO(request.content))
    zip_file.extractall(path=download_directory)
    os.chmod(local_executable_path, 0o755)
    return local_executable_path
