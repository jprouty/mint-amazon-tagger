#!/bin/bash

# exit when any command fails
set -e

echo "Setup the venv"
python3 -m venv release_venv
source release_venv/bin/activate
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt

echo "Open the app: verify it works"
fbs run

echo "Now make and upload the release"
fbs freeze
fbs installer
fbs upload
