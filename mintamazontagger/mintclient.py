import atexit
import getpass
import logging

from mintapi.api import Mint, MINT_ROOT_URL
from progress.bar import IncrementalBar
from progress.spinner import Spinner

from mintamazontagger.asyncprogress import AsyncProgress
from mintamazontagger.currency import micro_usd_to_usd_float

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


UPDATE_TRANS_ENDPOINT = '/updateTransaction.xevent'


class MintClient():

    def __init__(self, email=None, password=None,
                 session_path=None, headless=False, mfa_method='sms'):
        self.email = email
        self.password = password
        self.session_path = session_path
        self.headless = headless
        self.mfa_method = mfa_method

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

        logger.info('Logging into Mint')
        logger.info('You may be asked for an auth code at the command line! '
                    'Be sure to press ENTER after typing the 6 digit code.')

        mint_client = Mint.create(email, password,
                                  mfa_method=self.mfa_method,
                                  session_path=self.session_path,
                                  headless=self.headless)

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
        asyncSpin = AsyncProgress(Spinner('Fetching Categories '))
        categories = dict([
            (cat_dict['name'], cat_id)
            for (cat_id, cat_dict)
            in mint_api.get_categories().items()])
        asyncSpin.finish()
        return categories

    def get_transactions(self, start_date):
        start_date_str = start_date.strftime('%m/%d/%y')
        mint_api = self.get_mintapi()
        logger.info('Get all Mint transactions since {}.'.format(
            start_date_str))
        asyncSpin = AsyncProgress(Spinner('Fetching Transactions '))
        transactions = mint_api.get_transactions_json(
            start_date=start_date_str,
            include_investment=False,
            skip_duplicates=True)
        asyncSpin.finish()
        return transactions

    def send_updates(self, updates, ignore_category=False):
        mint_client = self.get_mintapi()
        updateProgress = IncrementalBar(
            'Updating Mint',
            max=len(updates))

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
                updateProgress.next()
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
                    # Based on the comment above, if the original transaction
                    # is a credit, flip the amount sign for things to work out!
                    if not orig_trans.is_debit:
                        amount *= -1
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

                updateProgress.next()
                logger.debug('Received response: {}'.format(response.text))
                num_requests += 1

        updateProgress.finish()

        logger.info('Sent {} updates to Mint'.format(num_requests))
