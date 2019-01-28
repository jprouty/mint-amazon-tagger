# 50 Micro dollars we'll consider equal (this allows for some
# division/multiplication rounding wiggle room).
MICRO_USD_EPS = 50
CENT_MICRO_USD = 10000

DOLLAR_EPS = 0.0001


def micro_usd_nearly_equal(micro_a, micro_b):
    return abs(micro_a - micro_b) < MICRO_USD_EPS


def round_usd(curr):
    return round(curr + DOLLAR_EPS, 2)


def round_micro_usd_to_cent(micro_usd):
    return int(round_usd(micro_usd_to_usd_float(micro_usd)) * 1000000)


def micro_usd_to_usd_float(micro_usd):
    return round_usd(micro_usd / 1000000.0)


def micro_usd_to_usd_string(micro_usd):
    return '{}${:.2f}'.format(
        '' if micro_usd >= -5000 else '-',
        micro_usd_to_usd_float(abs(micro_usd)))


def parse_usd_as_micro_usd(amount):
    return int(round_usd(parse_usd_as_float(amount)) * 1000000)


def parse_usd_as_float(amount):
    if not amount:
        return 0.0
    # Remove any formatting/grouping commas.
    amount = amount.replace(',', '')
    negate = False
    if '-' == amount[0]:
        negate = True
        amount = amount[1:]
    if '$' == amount[0]:
        amount = amount[1:]
    try:
        return float(amount) if not negate else -float(amount)
    except ValueError:
        return 0.0
