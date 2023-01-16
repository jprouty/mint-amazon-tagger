from datetime import datetime
import logging
import os
from pprint import pprint
import random
import requests
import time

from mintamazontagger.currency import micro_usd_to_float_usd
from mintamazontagger.webdriver import (
    get_element_by_id, get_element_by_xpath,
    get_element_by_link_text, get_elements_by_class_name,
    is_visible)

from mintapi.api import Mint

from selenium.common.exceptions import (
    ElementNotInteractableException, StaleElementReferenceException,
    TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

MINT_API_VERSION = 'pfm/v1'

MINT_HOME = 'https://mint.intuit.com'
MINT_OVERVIEW = f'{MINT_HOME}/overview'
MINT_TRANSACTIONS = f'{MINT_HOME}/{MINT_API_VERSION}/transactions'
MINT_CATEGORIES = f'{MINT_HOME}/{MINT_API_VERSION}/categories'


class MintClient():
    args = None
    webdriver_factory = None
    webdriver = None
    mint_api = None

    def __init__(self, args, webdriver_factory, mfa_input_callback=None):
        self.args = args
        self.webdriver_factory = webdriver_factory
        self.mfa_input_callback = mfa_input_callback

    def hasValidCredentialsForLogin(self):
        if self.args.mint_user_will_login:
            return True
        return self.args.mint_email and self.args.mint_password

    def login(self):
        if self.mint_api:
            return True
        if not self.hasValidCredentialsForLogin():
            logger.error('Missing Mint email or password.')
            return False

        logger.info('You may be asked for an auth code at the command line! '
                    'Be sure to press ENTER after typing the 6 digit code.')
        self.webdriver = self.webdriver_factory()
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
        return True

    def get_transactions(self, from_date=None, to_date=None):
        if not self.login():
            logger.error('Cannot login')
            return []
        logger.info(
            f'Getting all Mint transactions since {from_date} to {to_date}.')
        params = {
            'limit': '100000',
            'fromDate': from_date,
            'toDate': to_date,
        }

        response = self.webdriver.request(
            'GET', MINT_TRANSACTIONS, headers=self.mint_api._get_api_key_header(),
            params=params)
        if not _is_json_response_success('transactions', response):
            return []
        response_json = response.json()
        if not response_json['metaData']['totalSize']:
            logger.warning('No transactions found')
            return []
        if (response_json['metaData']['totalSize']
                > response_json['metaData']['pageSize']):
            # TODO(jprouty): Add pagination support.
            # Look at: response_json['metaData']['link'][1]['href']
            logger.error('More transactions are available than max page size '
                         '- try reducing the date range.')
        if self.args.mint_save_json:
            json_path = os.path.join(
                self.args.mint_json_location,
                f'Mint {int(time.time())} Transactions.json')
            logger.info(f'Saving Mint Transactions to json file: {json_path}')
            with open(json_path, "w") as json_out:
                pprint(response_json, json_out)
        # Remove all transactions that do not have a fiData message. These are
        # user entered expesnes and do not have a fiData entry.
        result = [trans for trans in response_json['Transaction']
                  if 'fiData' in trans]
        return result

    def get_categories(self):
        if not self.login():
            logger.error('Cannot login')
            return []
        logger.info('Getting Mint categories.')

        response = self.webdriver.request(
            'GET', MINT_CATEGORIES, headers=self.mint_api._get_api_key_header())
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
                    headers=self.mint_api._get_api_key_header())
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
                    headers=self.mint_api._get_api_key_header())
                logger.debug(f'Received response: {response.__dict__}')
                progress.next()
                num_requests += 1

        progress.finish()
        return num_requests


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
