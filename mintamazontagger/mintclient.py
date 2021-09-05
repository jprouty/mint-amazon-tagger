from datetime import date, datetime
import getpass
import json
import logging
import random
import requests
import time

from mintamazontagger.currency import micro_usd_to_usd_float
from mintamazontagger.webdriver import (
    get_element_by_id, get_element_by_name, get_element_by_xpath,
    get_element_by_link_text, get_elements_by_class_name, is_visible)

from selenium.common.exceptions import (
    StaleElementReferenceException, TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

MINT_HOME = 'https://mint.intuit.com'
MINT_OVERVIEW = '{}/overview.event'.format(MINT_HOME)
MINT_GET_TRANS = '{}/getJsonData.xevent'.format(MINT_HOME)
MINT_UPDATE_TRANS = '{}/updateTransaction.xevent'.format(MINT_HOME)
JSON_HEADER = {'accept': 'application/json'}


class MintClient():
    args = None
    webdriver_factory = None
    mfa_input_callback = None
    request_id = 0
    token = None
    webdriver = None
    logged_in = False

    def __init__(self, args, webdriver_factory, mfa_input_callback=None):
        self.args = args
        self.webdriver_factory = webdriver_factory
        self.mfa_input_callback = mfa_input_callback

    def login(self):
        if self.logged_in:
            return True
        if not self.args.mint_email and not self.args.mint_user_will_login:
            self.args.mint_email = input('Mint email: ')
        if not self.args.mint_password and not self.args.mint_user_will_login:
            self.args.mint_password = getpass.getpass('Mint password: ')
        if (not self.args.mint_email or not self.args.mint_password
                and not self.args.mint_user_will_login):
            logger.error('Missing Mint email or password.')
            return False

        logger.info('You may be asked for an auth code at the command line! '
                    'Be sure to press ENTER after typing the 6 digit code.')

        self.webdriver = self.webdriver_factory()
        self.logged_in = _nav_to_mint_and_login(
            self.webdriver, self.args, self.mfa_input_callback)
        if self.args.mint_wait_for_sync:
            _wait_for_sync(self.webdriver)
        return self.logged_in

    def get_transactions(self, start_date=None):
        if not self.login():
            logger.error('Cannot login')
            return []
        logger.info('Get all Mint transactions since {}.'.format(start_date))
        transactions = []
        offset = 0
        # Mint transactions are pagenated.
        while True:
            params = {
                'queryNew': '',
                'offset': offset,
                'comparableType': '8',
                'rnd': _get_random(),
                'task': 'transactions,txnfilters',
                'filterType': 'cash',
            }

            result = self.webdriver.request(
                'get', MINT_GET_TRANS, headers=JSON_HEADER, params=params)
            if result.status_code != requests.codes.ok:
                logger.error(
                    'Error getting transactions. status_code = {}'.format(
                        result.status_code))
                return result
            content_type = result.headers.get('content-type', '')
            if not content_type.startswith('application/json'):
                logger.error(
                    'Error getting transactions. content_type = {}'.format(
                        content_type))
                return result

            data = json.loads(result.text)
            partial_transactions = data['set'][0].get('data', [])
            if not partial_transactions:
                return transactions
            if start_date:
                last_date = _json_date_to_date(
                    partial_transactions[-1]['odate'])
                if last_date < start_date:
                    keep_txns = [
                        t for t in partial_transactions
                        if _json_date_to_date(t['odate']) >= start_date]
                    transactions.extend(keep_txns)
                    break
            transactions.extend(partial_transactions)
            offset += len(partial_transactions)
        return transactions

    def get_categories(self):
        if not self.login():
            logger.error('Cannot login')
            return {}
        logger.info('Getting Mint categories.')
        req_id = self.get_request_id_str()
        data = {
            'input': json.dumps([{
                'args': {
                    'excludedCategories': [],
                    'sortByPrecedence': False,
                    'categoryTypeFilter': 'FREE'
                },
                'id': req_id,
                'service': 'MintCategoryService',
                'task': 'getCategoryTreeDto2'
            }])
        }

        get_categories_url = (
            '{}/bundledServiceController.xevent?legacy=false&token={}'.format(
                MINT_HOME, self.get_token()))
        response = self.webdriver.request(
            'POST', get_categories_url, data=data, headers=JSON_HEADER).text
        if req_id not in response:
            logger.error(
                'Could not parse category data: "{}"'.format(response))
            return {}
        response = json.loads(response)
        response = response['response'][req_id]['response']

        result = {}
        for category in response['allCategories']:
            result[category['name']] = category['id']
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
                    'task': 'txnedit',
                    'txnId': '{}:0'.format(trans.id),
                    'note': trans.note,
                    'merchant': trans.merchant,
                    'token': self.get_token(),
                }
                if not ignore_category:
                    modify_trans = {
                        **modify_trans,
                        'category': trans.category,
                        'catId': trans.category_id,
                    }

                logger.debug(
                    'Sending a "modify" transaction request: {}'.format(
                        modify_trans))
                response = self.webdriver.request(
                    'POST', MINT_UPDATE_TRANS, data=modify_trans).text
                progress.next()
                logger.debug('Received response: {}'.format(response))
                num_requests += 1
            else:
                # Split the existing transaction into many.
                # If the existing transaction is a:
                #   - credit: positive amount is credit, negative debit
                #   - debit: positive amount is debit, negative credit
                itemized_split = {
                    'txnId': '{}:0'.format(orig_trans.id),
                    'task': 'split',
                    'data': '',  # Yup this is weird.
                    'token': self.get_token(),
                }
                all_credit = all(not trans.is_debit for trans in new_trans)

                for (i, trans) in enumerate(new_trans):
                    amount = trans.amount
                    # If it's a split credit, everything should be positive
                    if all_credit and amount < 0:
                        amount = -amount
                    amount = micro_usd_to_usd_float(amount)
                    itemized_split['amount{}'.format(i)] = amount
                    # Yup. Weird:
                    itemized_split['percentAmount{}'.format(i)] = amount
                    itemized_split['merchant{}'.format(i)] = trans.merchant
                    # Yup weird. '0' means new?
                    itemized_split['txnId{}'.format(i)] = 0
                    if not ignore_category:
                        itemized_split['category{}'.format(i)] = trans.category
                        itemized_split['categoryId{}'.format(i)] = (
                            trans.category_id)
                    else:
                        itemized_split['category{}'.format(i)] = (
                            orig_trans.category)
                        itemized_split['categoryId{}'.format(i)] = (
                            orig_trans.category_id)

                logger.debug(
                    'Sending a "split" transaction request: {}'.format(
                        itemized_split))
                response = self.webdriver.request(
                    'POST', MINT_UPDATE_TRANS, data=itemized_split)
                json_resp = response.json()
                # The first id is always the original transaction (now
                # parent transaction id).
                new_trans_ids = json_resp['txnId'][1:]
                assert len(new_trans_ids) == len(new_trans)
                for itemized_id, trans in zip(new_trans_ids, new_trans):
                    # Now send the note for each itemized transaction.
                    itemized_note = {
                        'task': 'txnedit',
                        'txnId': '{}:0'.format(itemized_id),
                        'note': trans.note,
                        'token': self.get_token(),
                    }
                    note_response = self.webdriver.request(
                        'POST', MINT_UPDATE_TRANS, data=itemized_note)
                    logger.debug(
                        'Received note response: {}'.format(
                            note_response.text))

                progress.next()
                logger.debug('Received response: {}'.format(response.text))
                num_requests += 1

        progress.finish()
        return num_requests

    def get_token(self):
        if self.token:
            return self.token
        value_json = self.webdriver.find_element_by_name(
            'javascript-user').get_attribute('value')
        self.token = json.loads(value_json)['token']
        return self.token

    def get_request_id_str(self):
        self.request_id += 1
        return str(self.request_id)


def _json_date_to_date(dateraw):
    cy = date.today().year
    try:
        newdate = datetime.strptime(dateraw + str(cy), '%b %d%Y')
    except ValueError:
        newdate = datetime.strptime(dateraw, '%m/%d/%y')
    return newdate.date()


# Never attempt to enter the password more than 2 times to prevent locking an
# account out due to too many fail attempts. A valid MFA can require reentry
# of the password.
_MAX_PASSWORD_ATTEMPTS = 2


def _nav_to_mint_and_login(webdriver, args, mfa_input_callback=None):
    webdriver.get(MINT_HOME)
    webdriver.implicitly_wait(2)

    sign_in_button = get_element_by_link_text(webdriver, 'Sign in')
    if not sign_in_button:
        logger.error('Cannot find "Sign in" button on Mint homepage.')
        return False
    sign_in_button.click()
    webdriver.implicitly_wait(2)

    # Mint login is a bit messy. Work through the flow, allowing for any order
    # of interstitials. Exit only when reaching the overview page (indicating
    # the user is logged in) or if the login timeout has been exceeded.
    #
    # For each attempt section, note that the element must both be present AND
    # visible. Mint renders but hides the complete login flow meaning that all
    # elements are always present (but only a subset are visible at any
    # moment).
    login_start_time = datetime.now()
    num_password_attempts = 0
    while not webdriver.current_url.startswith(MINT_OVERVIEW):
        since_start = datetime.now() - login_start_time
        if (args.mint_login_timeout and
                since_start.total_seconds() > args.mint_login_timeout):
            logger.error('Exceeded login timeout')
            return False

        if args.mint_user_will_login:
            _login_flow_advance(webdriver)
            continue

        userid_input = get_element_by_id(webdriver, 'ius-userid')
        identifier_input = get_element_by_id(webdriver, 'ius-identifier')
        password_input = get_element_by_id(webdriver, 'ius-password')
        submit_button = get_element_by_id(webdriver, 'ius-sign-in-submit-btn')
        # Password might be asked later in the MFA flow; combine logic here.
        mfa_password_input = get_element_by_id(
            webdriver, 'ius-sign-in-mfa-password-collection-current-password')
        mfa_submit_button = get_element_by_id(
            webdriver, 'ius-sign-in-mfa-password-collection-continue-btn')

        # Attempt to enter an email and/or password if the fields are present.
        do_submit = False
        if is_visible(userid_input):
            userid_input.clear()
            userid_input.send_keys(args.mint_email)
            logger.info('Mint Login Flow: Entering email into userid field')
            do_submit = True
        if is_visible(identifier_input):
            identifier_input.clear()
            identifier_input.send_keys(args.mint_email)
            logger.info('Mint Login Flow: Entering email into "id" field')
            do_submit = True
        if is_visible(password_input):
            num_password_attempts += 1
            password_input.send_keys(args.mint_password)
            logger.info('Mint Login Flow: Entering password')
            do_submit = True
        if is_visible(mfa_password_input):
            num_password_attempts += 1
            mfa_password_input.send_keys(args.mint_password)
            logger.info('Mint Login Flow: Entering password in MFA input')
            do_submit = True
        if num_password_attempts > _MAX_PASSWORD_ATTEMPTS:
            logger.error('Too many password entries attempted; aborting.')
            return False
        if do_submit:
            if is_visible(submit_button):
                logger.info('Mint Login Flow: Submitting login credentials')
                submit_button.submit()
            elif is_visible(mfa_submit_button):
                logger.info('Mint Login Flow: Submitting credentials for MFA')
                mfa_submit_button.submit()
            _login_flow_advance(webdriver)
            continue

        # Attempt to find the email on the account list page. This is often the
        # case when reusing a webdriver that has session state from a previous
        # run of the tool.
        known_accounts_selector = get_element_by_id(
            webdriver, 'ius-known-accounts-container')
        if is_visible(known_accounts_selector):
            usernames = get_elements_by_class_name(
                webdriver, 'ius-option-username')
            for username in usernames:
                if username.text == args.mint_email:
                    logger.info(
                        'Mint Login Flow: Selecting username from '
                        'multi-account selector.')
                    username.click()
                    _login_flow_advance(webdriver)
                    continue

        # If shown, bypass the "Let's add your current mobile number" modal.
        skip_phone_update_button = get_element_by_id(
            webdriver, 'ius-verified-user-update-btn-skip')
        if is_visible(skip_phone_update_button):
            logger.info(
                'Mint Login Flow: Skipping update user phone number modal.')
            skip_phone_update_button.click()

        # MFA method selector:
        mfa_options_form = get_element_by_id(webdriver, 'ius-mfa-options-form')
        if is_visible(mfa_options_form):
            # Attempt to use the user preferred method, falling back to the
            # first method.
            mfa_method_option = get_element_by_id(
                webdriver, 'ius-mfa-option-{}'.format(
                    args.mint_mfa_preferred_method))
            if is_visible(mfa_method_option):
                mfa_method_option.click()
                logger.info('Mint Login Flow: Selecting {} MFA method'.format(
                    args.mint_mfa_preferred_method))
            else:
                mfa_method_cards = get_elements_by_class_name(
                    webdriver, 'ius-mfa-card-challenge')
                if mfa_method_cards and len(mfa_method_cards) > 0:
                    mfa_method_cards[0].click()
            mfa_method_submit = get_element_by_id(
                webdriver, 'ius-mfa-options-submit-btn')
            if is_visible(mfa_method_submit):
                logger.info('Mint Login Flow: Submitting MFA method')
                mfa_method_submit.click()

        # MFA OTP Code:
        mfa_code_input = get_element_by_id(webdriver, 'ius-mfa-confirm-code')
        mfa_submit_button = get_element_by_id(
            webdriver, 'ius-mfa-otp-submit-btn')
        if is_visible(mfa_code_input) and is_visible(mfa_submit_button):
            mfa_code = (mfa_input_callback or input)(
                'Please enter your 6-digit MFA code: ')
            logger.info('Mint Login Flow: Entering MFA OTP code')
            mfa_code_input.send_keys(mfa_code)
            logger.info('Mint Login Flow: Submitting MFA OTP')
            mfa_submit_button.submit()

        # MFA soft token:
        mfa_token_input = get_element_by_id(webdriver, 'ius-mfa-soft-token')
        mfa_token_submit_button = get_element_by_id(
            webdriver, 'ius-mfa-soft-token-submit-btn')
        if is_visible(mfa_token_input) and is_visible(mfa_token_submit_button):
            import oathtool
            logger.info('Mint Login Flow: Generating soft token')
            mfa_code = oathtool.generate_otp(args.mfa_soft_token)
            logger.info('Mint Login Flow: Entering soft token into MFA input')
            mfa_token_input.send_keys(mfa_code)
            logger.info('Mint Login Flow: Submitting soft token MFA')
            mfa_token_submit_button.submit()

        # MFA account selector:
        mfa_select_account = get_element_by_id(
            webdriver, 'ius-mfa-select-account-section')
        mfa_token_submit_button = get_element_by_id(
            webdriver, 'ius-sign-in-mfa-select-account-continue-btn')
        if args.mint_intuit_account and is_visible(mfa_select_account):
            account_input = get_element_by_xpath(
                mfa_select_account,
                '//label/span[text()=\'{}\']/../'
                'preceding-sibling::input'.format(args.mint_intuit_account))
            if (is_visible(account_input) and
                    is_visible(mfa_token_submit_button)):
                account_input.click()
                mfa_token_submit_button.submit()
                logger.info('Mint Login Flow: MFA account selection')

    # Wait for the token to become available.
    while True:
        since_start = datetime.now() - login_start_time
        if (args.mint_login_timeout and
                since_start.total_seconds() > args.mint_login_timeout):
            logger.error('Exceeded login timeout')
            return False

        js_user = get_element_by_name(webdriver, 'javascript-user')
        if js_user:
            js_value = js_user.get_attribute('value')
            json_value = json.loads(js_value)
            if 'token' in json_value:
                # Token is ready; break out.
                break

        _login_flow_advance(webdriver)

    # If you made it here, you must be good to go!
    return True


def _login_flow_advance(webdriver):
    webdriver.implicitly_wait(1)
    time.sleep(1)


def _wait_for_sync(webdriver, wait_for_sync_timeout=5 * 60):
    try:
        status_message = WebDriverWait(webdriver, 30).until(
            expected_conditions.visibility_of_element_located(
                (By.CSS_SELECTOR, ".SummaryView .message")))
        WebDriverWait(webdriver, wait_for_sync_timeout).until(
            lambda x: ("Account refresh complete" in
                       status_message.get_attribute('innerHTML')))
    except (TimeoutException, StaleElementReferenceException):
        logger.warning("Mint sync apparently incomplete after timeout. "
                       "Data retrieved may not be current.")


def _get_random():
    return (str(int(time.mktime(datetime.now().timetuple()))) + str(
        random.randrange(999)).zfill(3))
