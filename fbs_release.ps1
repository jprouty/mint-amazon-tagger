#!/bin/bash

echo "Setup the venv"
python -m venv release_venv
release_venv\Scripts\activate.ps1
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt
pip install --upgrade -r requirements/windows.txt

echo "Clean it"
fbs clean

echo "Open the app: verify it works"
fbs run

echo "Now make and upload the release"
fbs freeze

echo "Now verify the built version works"
target/MintAmazonTagger.app/Contents/MacOS/MintAmazonTagger

fbs installer
fbs upload

rm -rf release_venv
