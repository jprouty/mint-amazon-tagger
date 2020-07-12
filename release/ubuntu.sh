#!/bin/bash

# exit when any command fails
set -e

cd "$(dirname "$0")/.."

# "app_name": "MintAmazonTagger",
# "author": "Jeff Prouty",
# "main_module": "src/main/python/mintamazontagger/main.py",
# "version": "1.0.6",
# "gpg_key": "CB1608BF9A09BE99908045E6E93C791A0BFE386F",
# "gpg_name": "Jeff Prouty",
# "url": "https://github.com/jprouty/mint-amazon-tagger"
# "categories": "Utility;",
# "description": "Mint Amazon tagger matches amazon purchases with your mint transactions, giving them useful descriptions.",
# "author_email": "jeff.prouty@gmail.com",

echo "Clean everything"
python3 setup.py clean

echo "Setup the release venv"
python -m venv release_venv
source release_venv/bin/activate
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt

# https://github.com/pypa/setuptools/issues/1963
pyinstaller \
  --name="MintAmazonTagger" \
  --windowed \
  --onefile \
  --hidden-import='pkg_resources.py2_warn' \
  mintamazontagger/main.py

deactivate
rm -rf release_venv
