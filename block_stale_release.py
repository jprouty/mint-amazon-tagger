from mintamazontagger import VERSION
from outdated import check_outdated


if __name__ == '__main__':
    try:
        is_stale, latest = check_outdated('mint-amazon-tagger', VERSION)
        print('Please update VERSION in __init__')
        exit(1)
    except ValueError:
        # If it's already up to date, this will throw
        exit(0)
