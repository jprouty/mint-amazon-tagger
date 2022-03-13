# Mint Transactions Tagger for Amazon Purchases

## Overview ##

Do you order a lot from Amazon? Tired of everything showing up as "Amazon"
w/ category "Shopping"? Then this tool is for you!

This tool *does not* save your username or password. It uses a tool call Selenium to drive a clean Chromium browser, which will then use the given Mint and Amazon credentials to log in as you, and then "drive" thought all the edits for you.

This tool *does not* require an Amazon store card/Visa. All you need is to pay for
your Amazon orders with an account that is sync'ed with Mint. For example, if
you alternate between 5 different credit cards to pay for purchases on your
Amazon account, only the transactions from credit cards sync'ed with Mint will
get tagged.

This tool takes Amazon order reports and merges it with your existing Mint
transactions. If it finds exact matches, it will either:

- Update the transaction description/category if there was only 1 item
- Split the transaction, one line-item per item in the order

The tagger will try to guess the best Mint category for you. It does this by
looking at each item's category from the Amazon Items report. Look at
`category.py` to see which Amazon categories map to which Mint categories.

After running the tagger, if you are not happy with the category,
simply change it! Next time you run the tagger, it will attempt to remember
your past personalized category and apply it to future purchases of the same
item. Caveats: this only works if item names match exactly. also, you must
change all (or the majority of) all the past, tagged examples of that item.
ie. if you only change 1 example and you have 10 purchases of that same item
it will take whatever the most common category used for that item.

The tagger will _NOT_ retag or touch transactions that have already been
tagged. So feel free to adjust categories after the fact without fear that the
next run will wipe everything out. However, if you _DO_ want to re-tag
previously tagged transactions, take a look at --retag_changed and
--prompt_retag arguments.

Some things the tagger cannot do:

- Amazon credit card award points are not reported anywhere in the order/item reports.
- Amazon gift cards are not yet supported (see #59)

## Install and Getting started ##

### EASIEST - Pre-built binaries ###

Please download the latest version from [github's releases page](https://github.com/jprouty/mint-amazon-tagger/releases)

### ADVANCED - Run from python source ###

#### Setup ####

1. `pip3 install mint-amazon-tagger`

2. To get the latest from time to time, update your version:
`pip3 install --upgrade mint-amazon-tagger`

3. Chromedriver should be fetched automatically. But if you run into issues,
try this:
```
# Mac:
brew tap homebrew/cask
brew cask install chromedriver

# Ubuntu/Debian:
# See also: https://askubuntu.com/questions/539498/where-does-chromedriver-install-to
sudo apt-get install chromium-chromedriver
```

#### Running - Full Auto GUI ####

This mode will fetch your Amazon Order History for you as well as tag mint.

1. `mint-amazon-tagger`

1. Plug in all your info into the app!

#### Running - Full Auto CLI ####

This mode will fetch your Amazon Order History for you as well as tag mint.

1. `mint-amazon-tagger-cli --amazon_email email@cool.com --mint_email couldbedifferent@aol.com`

#### Running - Semi-Auto ####

This mode requires you to fetch your Amazon Order History manually, then the
tagger automates the rest.

1. Generate and download your Amazon Order History Reports.

a. Login and visit [Amazon Order History
Reports](https://www.amazon.com/gp/b2b/reports)

b. "Request Report" for "Items", "Orders and shipments", and "Refunds". Make sure the
date ranges are the same.

c. Download the completed reports. Let's called them
`Items.csv Orders.csv Refunds.csv` for this walk-through. Note that
Refunds is optional! Yay.

2. (Optional) Do a dry run! Make sure everything looks right first. Run:
`mint-amazon-tagger-cli --items_csv Items.csv --orders_csv Orders.csv --refunds_csv Refunds.csv --dry_run --mint_email yourEmail@here.com`

3. Now perform the actual updates, without `--dry_run`:
`mint-amazon-tagger-cli --items_csv Items.csv --orders_csv Orders.csv --refunds_csv Refunds.csv --mint_email yourEmail@here.com`

4. Sit back and relax! The run time depends on the speed of your machine,
quality of internet connection, and total number of transactions. For
reference, my machine did about 14k Mint transactions, finding 2k Amazon
matches in under 10 minutes.

To see all options, see:
`mint-amazon-tagger-cli --help`

## Tips and Tricks ##

Not every bank treats Amazon purchases the same, or processes transactions as quickly as others. If you're having a low match rate (look at the terminal output after completion), then try adjusting some of the command line flags. To see a complete list, run `mint-amazon-tagger-cli --help`.

Some common options to try:

* --mint_input_include_mmerchant and/or --mint_input_include_merchant. This allows for more generous consideration of Mint transactions for matching. See [more context here](https://github.com/jprouty/mint-amazon-tagger/issues/50)
* --max_days_between_payment_and_shipping. If your bank is slow at posting payments, adjusting this value up to 7 or more will increase your chance of matching. If you have a high volume of purchases, this can increase your chance of mis-tagging items.
