import logging
import os
from pprint import pprint
import requests
import time

from mintamazontagger.currency import micro_usd_to_float_usd

from mintapi.api import Mint

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

MINT_HOME = 'https://mint.intuit.com'
MINT_OVERVIEW = f'{MINT_HOME}/overview'
MINT_API_ENDPOINT = f'{MINT_HOME}/pfm'
MINT_API_VERSION = 'v1'
MINT_TRANSACTIONS = f'{MINT_API_ENDPOINT}/{MINT_API_VERSION}/transactions'
MINT_CATEGORIES = f'{MINT_API_ENDPOINT}/{MINT_API_VERSION}/categories'


class MintClient():
    args = None
    webdriver_factory = None
    webdriver = None
    mint_api = None
    # To signify a successful user login when args.mint_user_will_login is present.
    user_login_success = False

    def __init__(self, args, webdriver_factory, mfa_input_callback=None):
        self.args = args
        self.webdriver_factory = webdriver_factory
        self.mfa_input_callback = mfa_input_callback

    def hasValidCredentialsForLogin(self):
        if self.args.mint_user_will_login:
            return True
        return self.args.mint_email and self.args.mint_password

    def get_api_header(self):
        # Defer to MintAPI if present.
        if self.mint_api:
            return self.mint_api._get_api_key_header()
        # Otherwise, attempt ourselves (needed in the case of args.mint_user_will_login).
        return _get_api_header(self.webdriver)

    def is_logged_in(self):
        return self.mint_api or self.user_login_success

    def login(self):
        if self.is_logged_in():
            return True
        if not self.hasValidCredentialsForLogin():
            logger.error('Missing Mint email or password.')
            return False

        self.webdriver = self.webdriver_factory()

        if self.args.mint_user_will_login:
            logger.info(
                'Mint Login Flow: login to be performed manually by the user')
            self.webdriver.get(MINT_HOME)
            self.user_login_success = _await_user_login(
                self.webdriver, self.args.mint_login_timeout)
        else:
            logger.info('Mint Login Flow: MintAPI to complete login')
            logger.info('You may be asked for an auth code at the command line! '
                        'Be sure to press ENTER after typing the 6 digit code.')
            self.mint_api = Mint(
                driver=self.webdriver,
                email=self.args.mint_email,
                password=self.args.mint_password,
                mfa_method=self.args.mint_mfa_preferred_method,
                mfa_token=self.args.mint_mfa_soft_token,
                mfa_input_callback=self.mfa_input_callback,
                intuit_account=self.args.mint_intuit_account,
                wait_for_sync=self.args.mint_wait_for_sync,
                wait_for_sync_timeout=self.args.mint_sync_timeout,
                quit_driver_on_fail=False,
            )
        # Use our own wait for sync logic in both cases, to protect making api calls too soon.
        _wait_for_overview_loaded(self.webdriver, self.args.mint_wait_for_sync)
        return self.is_logged_in()

    def get_transactions(self, from_date=None, to_date=None):
        if not self.login():
            logger.error('Cannot login')
            return []
        logger.info(
            f'Getting all Mint transactions since {from_date} to {to_date}.')
        
        limit = 10000
        params = {
            'limit': limit,
            'fromDate': from_date,
            'toDate': to_date,
        }
        response = self.webdriver.request(
            'GET', MINT_TRANSACTIONS, headers=self.get_api_header(),
            params=params)
        results = []

        while True:
            if not _is_json_response_success('transactions', response):
                return results
            response_json = response.json()
            if not response_json['metaData']['totalSize']:
                logger.warning('No transactions found')
                return results
            if self.args.mint_save_json:
                json_path = os.path.join(
                    self.args.mint_json_location,
                    f'Mint {int(time.time())} Transactions.json')
                logger.info(f'Saving Mint Transactions to json file: {json_path}')
                with open(json_path, "w") as json_out:
                    pprint(response_json, json_out)
            # Remove all transactions that do not have a fiData message. These are
            # user entered expenses and do not have a fiData entry.
            results.extend([trans for trans in response_json['Transaction']
                            if 'fiData' in trans])
            
            page_size = response_json['metaData']['pageSize']
            total_records = response_json['metaData']['totalSize']

            next_page = _get_next_link_href(response_json['metaData']['link'])
            if not next_page:
                # No more transactions.
                return results
            else:
                next_page_url = f'{MINT_API_ENDPOINT}/{next_page}'
                response = self.webdriver.request(
                    'GET', next_page_url, headers=self.get_api_header())
            
    def get_categories(self):
        if not self.login():
            logger.error('Cannot login')
            return []
        logger.info('Getting Mint categories.')

        response = self.webdriver.request(
            'GET', MINT_CATEGORIES, headers=self.get_api_header())
        if not _is_json_response_success('categories', response):
            return []
        response_json = response.json()
        if not response_json['metaData']['totalSize']:
            logger.error('No categories found')
            return []
        if self.args.mint_save_json:
            json_path = os.path.join(
                self.args.mint_json_location,
                f'Mint {int(time.time())} Categories.json')
            logger.info(f'Saving Mint Categories to json file: {json_path}')
            with open(json_path, "w") as json_out:
                pprint(response_json, json_out)
        result = {}
        for cat in response_json['Category']:
            result[cat['name']] = cat
        return result

    def send_updates(self, updates, progress, ignore_category=False):
        if not self.login():
            logger.error('Cannot login')
            return 0
        num_requests = 0
        for (orig_trans, new_trans) in updates:
            if len(new_trans) == 1:
                # Update the existing transaction.
                trans = new_trans[0]
                modify_trans = {
                    'type': trans.type,
                    'description': trans.description,
                    'notes': trans.notes,
                }
                if not ignore_category:
                    modify_trans = {
                        **modify_trans,
                        'category': {'id': trans.category.id},
                    }

                logger.debug(
                    f'Sending a "modify" transaction request: {modify_trans}')
                response = self.webdriver.request(
                    'PUT',
                    f'{MINT_TRANSACTIONS}/{trans.id}',
                    json=modify_trans,
                    headers=self.get_api_header())
                logger.debug(f'Received response: {response.__dict__}')
                progress.next()
                num_requests += 1
            else:
                # Split the existing transaction into many.
                split_children = []
                for trans in new_trans:
                    category = (orig_trans.category if ignore_category
                                else trans.category)
                    itemized_split = {
                        'amount': f'{micro_usd_to_float_usd(trans.amount)}',
                        'description': trans.description,
                        'category': {'id': category.id, 'name': category.name},
                        'notes': trans.notes,
                    }
                    split_children.append(itemized_split)

                split_edit = {
                    'type': orig_trans.type,
                    'amount': micro_usd_to_float_usd(orig_trans.amount),
                    'splitData': {'children': split_children},
                }
                logger.debug(
                    f'Sending a "split" transaction request: {split_edit}')
                response = self.webdriver.request(
                    'PUT',
                    f'{MINT_TRANSACTIONS}/{trans.id}',
                    json=split_edit,
                    headers=self.get_api_header())
                logger.debug(f'Received response: {response.__dict__}')
                progress.next()
                num_requests += 1

        progress.finish()
        return num_requests


def _get_next_link_href(links):
    for l in links:
        if l['rel'] == 'next':
            return l['href']
    return None


def _is_json_response_success(request_string, response):
    if response.status_code != requests.codes.ok:
        logger.error(
            f'Error getting {request_string}. '
            f'status_code = {response.status_code}')
        return False
    content_type = response.headers.get('content-type', '')
    if not content_type.startswith('application/json'):
        logger.error(
            f'Error getting {request_string}. content_type = {content_type}')
        return False
    return True


def _get_api_header(webdriver):
    api_key = webdriver.execute_script(
        "return window.__shellInternal.appExperience.appApiKey")
    auth = f'Intuit_APIKey intuit_apikey={api_key}, intuit_apikey_version=1.0'
    return {
        'authorization': auth,
        'accept': 'application/json',
    }


def _await_user_login(webdriver, timeout):
    try:
        WebDriverWait(webdriver, timeout).until(EC.url_contains(MINT_OVERVIEW))
        return True
    except TimeoutException:
        logger.info(
            f'Mint Login Flow: User login did not complete within {timeout} '
            'seconds. Tool is looking for the account overview page before '
            'proceeding.')
        return False


def _wait_for_overview_loaded(
        webdriver, wait_for_sync=False, wait_for_sync_timeout=5 * 60):
    logger.info('Waiting for Mint Overview')
    try:
        # Wait for the accounts list to present before continuing.
        WebDriverWait(webdriver, 30).until(
            EC.visibility_of_element_located(
                (By.XPATH, '//span[text()="Accounts"]')))
        logger.info('Mint overview loaded')
        if (wait_for_sync):
            logger.info('Waiting for Mint to sync accounts')
            WebDriverWait(webdriver, wait_for_sync_timeout).until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//strong[text()="Account refresh complete."]')))
            logger.info('Mint account sync complete')
    except (TimeoutException, StaleElementReferenceException):
        logger.warning("Mint sync apparently incomplete after timeout. "
                       "Data retrieved may not be current.")
