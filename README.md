# Mint Transactions Tagger for Amazon Purchases

## Overview ##

Do you order a lot from Amazon? Tired of everything showing up as "Amazon"
and category "Shopping" in mint.com? Then this tool is for you!

This tool requests "Amazon Order History Reports" on your behalf and matches
your order history with your Mint transactions. If it finds an exact matches
it will either:

- Update the transaction description and category if there was only 1 item
- Itemize the transaction - one line-item per item in the order (via Mint transaction splits)

The tagger will try to guess the best Mint category for you. The tool will
lookup the best category via each item's UNSPSC category code. See
`category.py` for the UNSPSC code to Mint category mappings.

If the tool chooses poor categories for your transactions simply change it! The next time you run the tool it will remember your past personalized category edits and attempt to apply it to future purchases of the same
item. This only works if the item names match exactly. Also, you must
change all (or the majority of) all the past, tagged examples of that item for the tool to pick up the hint. Put another way: if you only change 1 past transaction and you have 10 purchases of that same item the tool will take whatever the most common category used for that item.

This tool **will not** retag or touch transactions that have already been
tagged. Feel free to adjust categories after the fact without fear that the
next run will wipe everything out. If you do want to re-tag
previously tagged transactions take a look at the retag_changed option.

This tool **does not** save your username or password. The tool is powered by the [Selenium framework](https://www.selenium.dev/) to automate an instance of the Chrome/Chromium browser. When running the tool it will prompt for the username and password and then enter it into the browser for you. There are options that allow for manual user operation of the login flows for both Mint and Amazon.

This tool **does not** require an Amazon store card/Visa. All you need is to pay for your Amazon orders with an account that is synchronized with Mint. For example, if you alternate between 5 different credit cards to pay for purchases on your Amazon account, only the transactions from credit cards synchronized with Mint will get tagged.

Some things the tagger cannot do:

- Amazon credit card award points are not reported anywhere in the order/item reports.
- Amazon gift cards are not yet supported (see [issue #59](https://github.com/jprouty/mint-amazon-tagger/issues/59))

## Support ##

This project has been a passion project of [mine](https://github.com/jprouty) to better understand cashflow (critical to trend analysis and budgeting).

If you have found this tool useful, please consider showing your support:

- [CashApp](https://cash.app/$JeffProuty)
- [Venmo](https://www.venmo.com/u/jeff-prouty)
- [Paypal.me](https://paypal.me/jeffprouty)
- [Patreon](https://patreon.com/jeffprouty) - **recurring**
- Bitcoin - BTC Address: `3JfvxXzJJ85pxk7wnUmjTUKc6MfDXFWjpg`
- Ethereum - ETH Address: `0xFcd385b3D18DABa5231a64EEA2327fE1F1b1Ff15`

## Install and Getting started ##

### EASIEST - Pre-built binaries ###

Please download the latest version from [github's releases page](https://github.com/jprouty/mint-amazon-tagger/releases)

### ADVANCED - Docker Headless CLI ###

You can run the Mint Amazon Tagger via docker like so:

```
# Check out this git repo if not already:
git clone https://github.com/jprouty/mint-amazon-tagger.git
cd mint-amazon-tagger

# Build the image:
docker build -t mint-amazon-tagger .

# Run the container:
docker run -it --rm mint-amazon-tagger
```

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

Not every bank treats Amazon purchases the same, or processes transactions as quickly as others. If you are having a low match rate (look at the terminal output after completion), then try adjusting some of the options or command line flags. To see a complete list, run `mint-amazon-tagger-cli --help`.

Some common options to try:

- --mint_input_include_inferred_description. This allows for more generous consideration of Mint transactions for matching. See [more context here](https://github.com/jprouty/mint-amazon-tagger/issues/50)
- --mint_input_include_user_description. Similar to above; considers the current description as shown in the Mint tool (including any user edits).
- --max_days_between_payment_and_shipping. If your bank is slow at posting payments, adjusting this value up to 7 or more will increase your chance of matching. If you have a high volume of purchases, this can increase your chance of mis-tagging items.
