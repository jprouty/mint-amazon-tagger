import argparse
import datetime
import os


def get_name_to_help_dict(parser):
    return dict([(a.dest, a.help) for a in parser._actions])


def define_common_args(parser):
    """Parseargs shared between both CLI & GUI programs."""
    home = os.path.expanduser("~")
    # Amazon creds:
    parser.add_argument(
        '--amazon_email', default=None,
        help=('Amazon e-mail. If not provided, you will be '
              'prompted for it.'))
    parser.add_argument(
        '--amazon_password', default=None,
        help=('Amazon password. If not provided, you will be '
              'prompted for it.'))

    parser.add_argument(
        '--order_history_start_date',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'),
        default=datetime.date.today() - datetime.timedelta(days=90),
        help=('The start date for fetching Amazon order history. Defaults to '
              '90 days ago from today. Format: YYYY-MM-DD'))
    parser.add_argument(
        '--order_history_end_date',
        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'),
        default=datetime.date.today(),
        help=('The end date for fetching Amazon order history. Defaults to '
              'today. Format: YYYY-MM-DD'))

    default_report_location = os.path.join(home, 'Amazon Order Reports')
    parser.add_argument(
        '--report_download_location', type=str,
        default=default_report_location,
        help='Where to place the downloaded reports.')

    # Amazon Input, as CSV file:
    parser.add_argument(
        '--items_csv', type=argparse.FileType('r'),
        help=('The "Items" Order History Report from Amazon. If not present, '
              'will try to fetch order history for you. See --amazon_email.'))
    parser.add_argument(
        '--orders_csv', type=argparse.FileType('r'),
        help='The "Orders and Shipments" Order History Report from Amazon')
    parser.add_argument(
        '--refunds_csv', type=argparse.FileType('r'),
        help='The "Refunds" Order History Report from Amazon. '
             'This is optional.')

    # Mint creds:
    parser.add_argument(
        '--mint_email', default=None,
        help=('Mint e-mail address for login. If not provided here, will be '
              'prompted for user.'))
    parser.add_argument(
        '--mint_password', default=None,
        help=('Mint password for login. If not provided here, will be '
              'prompted for.'))
    parser.add_argument(
        '--mint_mfa_method',
        default='sms',
        choices=['sms', 'email'],
        help='The Mint MFA method (2factor auth codes).')
    parser.add_argument(
        '--mint_wait_for_sync',
        action='store_true',
        default=False,
        help=('Wait for Mint accounts to sync, up to 5 minutes. By '
              'default, do not wait for accounts to sync with the backing '
              'financial institutions.'))

    # Mint API options:
    default_session_path = os.path.join(home, '.mintapi', 'session')
    parser.add_argument(
        '--session-path', nargs='?',
        default=default_session_path,
        help=('Directory to save browser session, including cookies. Use to '
              'prevent repeated MFA prompts. Defaults to ~/.mintapi/session. '
              'Set to None to use a temporary profile.'))
    parser.add_argument(
        '--headless',
        action='store_true',
        default=False,
        help='Whether to execute chromedriver with no visible window.')

    # Prefix customization:
    parser.add_argument(
        '--description_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'transaction. By default, the \'Website\' value from Amazon '
              'Items/Orders csv is used. If a string is provided, use '
              'this instead for all matched transactions. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'transaction has already been tagged by this tool.'))
    parser.add_argument(
        '--description_return_prefix_override', type=str,
        help=('The prefix to use when updating the description for each Mint '
              'refund. By default, the \'Website\' value from Amazon '
              'Items/Orders csv is used with refund appended (e.g. '
              '\'Amazon.com Refund: ...\'. If a string is provided here, use '
              'this instead for all matched refunds. If given, this is '
              'used in conjunction with amazon_domains to detect if a '
              'refund has already been tagged by this tool.'))
    parser.add_argument(
        '--amazon_domains', type=str,
        # From: https://en.wikipedia.org/wiki/Amazon_(company)#Website
        default=('amazon.com,amazon.cn,amazon.in,amazon.co.jp,amazon.com.sg,'
                 'amazon.com.tr,amazon.fr,amazon.de,amazon.it,amazon.nl,'
                 'amazon.es,amazon.co.uk,amazon.ca,amazon.com.mx,'
                 'amazon.com.au,amazon.com.br'),
        help=('A list of all valid Amazon domains/websites. These should '
              'match the website column from Items/Orders and is used to '
              'detect if a transaction has already been tagged by this tool.'))

    # To itemize or not to itemize; that is the question:
    parser.add_argument(
        '--verbose_itemize', action='store_true',
        help=('Itemize everything, instead of the default behavior, which is '
              'to not itemize out shipping/promos/etc if '
              'there is only one item per Mint transaction. Will also remove '
              'free shipping.'))
    parser.add_argument(
        '--no_itemize', action='store_true',
        help=('Do not split Mint transactions into individual items with '
              'attempted categorization.'))

    parser.add_argument(
        '--num_updates', type=int,
        default=0,
        help=('Only send the first N updates to Mint (or print N updates at '
              'dry run). If not present, all updates are sent or printed.'))
    parser.add_argument(
        '--retag_changed', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category). This '
              'feature works by looking for "Amazon.com: " at the start of a '
              'transaction. If the user changes the description, then the '
              'tagger won\'t know to leave it alone.'))

    # Tagging options:
    parser.add_argument(
        '--no_tag_categories', action='store_true',
        help=('Do not update Mint categories. This is useful as '
              'Amazon doesn\'t provide the best categorization and it is '
              'pretty common user behavior to manually change the categories. '
              'This flag prevents tagger from wiping out that user work.'))
    parser.add_argument(
        '--do_not_predict_categories', action='store_true',
        help=('Do not attempt to predict custom category tagging based on any '
              'tagging overrides. By default (no arg) tagger will attempt to '
              'find items that you have manually changed categories for.'))
    parser.add_argument(
        '--max_days_between_payment_and_shipping', type=int,
        default=3,
        help=('How many days are allowed to pass between when Amazon has '
              'shipped an order and when the payment has posted to your '
              'bank account (as per Mint\'s view).'))
    parser.add_argument(
        '--mint_input_merchant_filter', type=str,
        default='amazon,amzn',
        help=('Only consider Mint transactions that have one of these strings '
              'in the merchant field. Case-insensitive comma-separated.'))
    parser.add_argument(
        '--mint_input_include_mmerchant', action='store_true',
        help=('Consider using the mmerchant field when determining if '
              'a transaction is an Amazon purchase. This can be necessary '
              'when your bank renames transactions to "Debit card payment". '
              'Mint sometimes auto-recovers these into "Amazon", and flipping '
              'this flag will help match these. To know if you should use it, '
              'find a transaction in the Mint tool, and click on the details. '
              'Look for "Appears on your BANK ACCOUNT NAME statement as NOT '
              'USEFUL NAME on DATE".'))
    parser.add_argument(
        '--mint_input_include_merchant', action='store_true',
        help=('Consider using the merchant field when determining if '
              'a transaction is an Amazon purchase. This is similar to '
              '--mint_input_include_mmerchant but also includes any user '
              'edits to the transaction name.'))
    parser.add_argument(
        '--mint_input_categories_filter', type=str,
        help=('Only consider Mint transactions that match one of '
              'the given categories here. Comma separated list of Mint '
              'categories.'))

    parser.add_argument(
        '--save_pickle_backup', action='store_true',
        default=False,
        help=('Saves a backup of your Mint transactions to a python "Pickle" '
              'file, just in case anything goes wrong or for rapid '
              'development so you don\'t have to download from Mint every '
              'time the tool is run. Off by default to prevent storing '
              'sensitive information locally without a user knowing it.'))
    parser.add_argument(
        '--pickled_epoch', type=int,
        help=('Do not fetch categories or transactions from Mint. Use this '
              'pickled epoch instead. If coupled with --dry_run, no '
              'connection to Mint is established.'))
    default_pickle_path = os.path.join(home, 'Mint Backup')
    parser.add_argument(
        '--mint_pickle_location', type=str,
        default=default_pickle_path,
        help='Where to store the fetched Mint pickles (for backup).')


def define_gui_args(parser):
    define_common_args(parser)

    # TODO: Clean up and remove.
    parser.add_argument(
        '--prompt_retag',
        default=False,
        action='store_false',
        help=('Unsupported for gui; but must be defined to false.'))


def define_cli_args(parser):
    define_common_args(parser)

    # Debugging/testing.
    parser.add_argument(
        '--dry_run', action='store_true',
        help=('Do not modify Mint transaction; instead print the proposed '
              'changes to console.'))
    parser.add_argument(
        '--skip_dry_print', action='store_true',
        help=('Do not print dry run results (useful for development).'))

    parser.add_argument(
        '-V', '--version', action='store_true',
        help='Shows the app version and quits.')

    # Retag transactions that have already been tagged previously:
    parser.add_argument(
        '--prompt_retag', action='store_true',
        help=('For transactions that have been previously tagged by this '
              'script, override any edits (like adjusting the category) but '
              'only after confirming each change. More gentle than '
              '--retag_changed'))
    parser.add_argument(
        '--print_unmatched', action='store_true',
        help=('At completion, print unmatched orders to help manual tagging.'))
