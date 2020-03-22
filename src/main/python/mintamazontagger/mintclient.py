import atexit
import getpass
import logging
import os

from mintapi.api import Mint, MINT_ROOT_URL

from mintamazontagger.currency import micro_usd_to_usd_float

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


UPDATE_TRANS_ENDPOINT = '/updateTransaction.xevent'


class NoProgress:
    def next(self, i=1):
        pass

    def finish(self):
        pass


class MintClient():
    def __init__(
            self,
            email=None, password=None,
            session_path=None, headless=False, mfa_method='sms',
            wait_for_sync=False, mfa_input_callback=None,
            progress_factory=lambda msg, max: NoProgress()):
        self.email = email
        self.password = password
        self.session_path = session_path
        self.headless = headless
        self.mfa_method = mfa_method
        self.mfa_input_callback = mfa_input_callback
        self.wait_for_sync = wait_for_sync
        self.progress_factory = progress_factory

        self.mintapi = None

    def close(self):
        if self.mintapi:
            self.mintapi.close()
            self.mintapi = None

    def get_mintapi(self):
        if self.mintapi:
            return self.mintapi

        email = self.email
        password = self.password

        if not email:
            email = input('Mint email: ')

        if not password:
            password = getpass.getpass('Mint password: ')

        if not email or not password:
            logger.error('Missing Mint email or password.')
            exit(1)

        logger.info('You may be asked for an auth code at the command line! '
                    'Be sure to press ENTER after typing the 6 digit code.')

        login_progress = self.progress_factory('Logging into Mint', 0)
        # The cwd when installed on a users system is typically not writable.
        # HACK: Pass through desired download location once that's supported.
        cwd = os.getcwd()
        os.chdir(os.path.expanduser("~"))
        mint_client = Mint.create(email, password,
                                  mfa_method=self.mfa_method,
                                  mfa_input_callback=self.mfa_input_callback,
                                  session_path=self.session_path,
                                  headless=self.headless,
                                  wait_for_sync=self.wait_for_sync)
        os.chdir(cwd)
        login_progress.finish()

        def close_mint_client():
            if mint_client:
                mint_client.close()

        atexit.register(close_mint_client)

        self.mintapi = mint_client
        return mint_client

    def get_categories(self):
        # Create a map of Mint category name to category id.
        logger.info('Creating Mint Category Map.')
        mint_api = self.get_mintapi()
        categories = dict([
            (cat_dict['name'], cat_id)
            for (cat_id, cat_dict)
            in mint_api.get_categories().items()])
        return categories

    def get_transactions(self, start_date):
        start_date_str = start_date.strftime('%m/%d/%y')
        mint_api = self.get_mintapi()
        logger.info('Get all Mint transactions since {}.'.format(
            start_date_str))
        transactions = mint_api.get_transactions_json(
            start_date=start_date_str,
            include_investment=False,
            skip_duplicates=True)
        return transactions

    def send_updates(self, updates, progress, ignore_category=False):
        mint_client = self.get_mintapi()
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
                    'token': mint_client.token,
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
                response = mint_client.post(
                    '{}{}'.format(
                        MINT_ROOT_URL,
                        UPDATE_TRANS_ENDPOINT),
                    data=modify_trans).text
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
                    'token': mint_client.token,
                }
                for (i, trans) in enumerate(new_trans):
                    amount = trans.amount
                    if not trans.is_debit:
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
                response = mint_client.post(
                    '{}{}'.format(
                        MINT_ROOT_URL,
                        UPDATE_TRANS_ENDPOINT),
                    data=itemized_split)
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
                        'token': mint_client.token,
                    }
                    note_response = mint_client.post(
                        '{}{}'.format(
                            MINT_ROOT_URL,
                            UPDATE_TRANS_ENDPOINT),
                        data=itemized_note)
                    logger.debug(
                        'Received note response: {}'.format(
                            note_response.text))

                progress.next()
                logger.debug('Received response: {}'.format(response.text))
                num_requests += 1

        progress.finish()
        return num_requests
