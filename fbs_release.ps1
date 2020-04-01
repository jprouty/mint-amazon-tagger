#!/bin/bash

# venv not working for me at the moment.
# echo "Setup the venv"
# python -m venv release_venv
# release_venv\Scripts\activate.ps1

pip install --upgrade pip
pip install --upgrade -r requirements/base.txt
pip install --upgrade -r requirements/windows.txt

echo "Clean it"
fbs clean

echo "Run the app: verify it works"
fbs run

echo "Now freeze the app"
fbs freeze

echo "Now verify the built version works"
target\MintAmazonTagger\MintAmazonTagger.exe

echo "Signing the app"
fbs sign

echo "Building the installer"
fbs installer

echo "Signing the installer"
fbs sign_installer

echo "Uploading"
fbs upload

# deactive
# rm -rf release_venv
