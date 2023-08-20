import logging
import psutil

from selenium.common.exceptions import (
    InvalidArgumentException, NoSuchElementException)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
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
    try:
        return Chrome(options=chrome_options)
    except InvalidArgumentException as e:
        if 'user data directory is already in use' not in e.msg:
            logger.warning('reraising selenium exception')
            raise e
        logger.warn(
            'Found existing webdriver from previous run, attempting to kill')
        for proc in psutil.process_iter():
            try:
                if not proc.children():
                    continue
                if not any(
                        [session_path in param for param in proc.cmdline()]):
                    continue
                logger.info(
                    f'Attempting to terminate process id {proc.pid}')
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.ZombieProcess):
                pass
        return Chrome(options=chrome_options)


def is_visible(element):
    return element and element.is_displayed()


def get_element_by_id(driver, id):
    try:
        return driver.find_element(By.ID, id)
    except NoSuchElementException:
        pass
    return None


def get_element_by_name(driver, name):
    try:
        return driver.find_element(By.NAME, name)
    except NoSuchElementException:
        pass
    return None


def get_element_by_xpath(driver, xpath):
    try:
        return driver.find_element(By.XPATH, xpath)
    except NoSuchElementException:
        pass
    return None


def get_element_by_link_text(driver, link_text):
    try:
        return driver.find_element(By.LINK_TEXT, link_text)
    except NoSuchElementException:
        pass
    return None


def get_elements_by_class_name(driver, class_name):
    try:
        return driver.find_elements(By.CLASS_NAME, class_name)
    except NoSuchElementException:
        pass
    return None
