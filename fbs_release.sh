#!/bin/bash

# exit when any command fails
set -e

echo "Setup the venv"
python3 -m venv release_venv
source release_venv/bin/activate
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt

echo "Clean it"
fbs clean

echo "Open the app: verify it works"
fbs run

echo "Now freeze the app"
fbs freeze

echo "Now verify the built version works"
if [[ "$OSTYPE" == "linux-gnu" ]]; then
    target/MintAmazonTagger/MintAmazonTagger
else
    target/MintAmazonTagger.app/Contents/MacOS/MintAmazonTagger
fi

fbs installer
fbs upload

deactivate

rm -rf release_venv
