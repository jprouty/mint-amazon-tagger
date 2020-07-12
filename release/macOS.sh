#!/bin/bash

# "mac_bundle_identifier": "com.jeffprouty.mintamazontagger"
# "app_name": "MintAmazonTagger",
# "author": "Jeff Prouty",
# "main_module": "src/main/python/mintamazontagger/main.py",
# "version": "1.0.6",
# "gpg_key": "CB1608BF9A09BE99908045E6E93C791A0BFE386F",
# "gpg_name": "Jeff Prouty",
# "url": "https://github.com/jprouty/mint-amazon-tagger"

# exit when any command fails
set -e

cd "$(dirname "$0")/.."

echo "Clean everything"
python3 setup.py clean

echo "Setup the release venv"
python -m venv release_venv
source release_venv/bin/activate
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt

# https://github.com/pypa/setuptools/issues/1963
# ?? --hidden-import='pkg_resources.py2_warn' \
pyinstaller \
  --name="MintAmazonTagger" \
  --windowed \
  --onefile \
  mintamazontagger/main.py

deactivate
rm -rf release_venv

echo "Signing the app"
APP_IDENTITY="Developer ID Application: Jeff Prouty (NRC455QXS5)"
EMAIL="jeff.prouty@gmail.com"

codesign --verify --verbose --force --deep --sign "${APP_IDENTITY}" --entitlements entitlements.plist --options=runtime target/MintAmazonTagger.app

echo "Creating installer"
## TODO

# echo "Signing installer"
# codesign --verify --verbose --force --deep --sign "${APP_IDENTITY}" --entitlements entitlements.plist --options=runtime target/MintAmazonTagger.dmg

echo "Creating an Apple notary request"
# xcrun altool --notarize-app \
#     --primary-bundle-id "com.jeffprouty.mintamazontagger" \
#     --username "${EMAIL}" \
#     --password "@keychain:AC_PASSWORD" \
#     --file target/MintAmazonTagger.dmg

echo "Check notary status later via: "
echo "xcrun altool --notarization-history 0 -u \"${EMAIL}\" -p \"@keychain:AC_PASSWORD\""
echo "  AND"
echo "xcrun altool --notarization-info <REQUEST_ID> -u \"${EMAIL}\""
echo
echo "Once successful, staple and re-upload:"
echo "xcrun stapler staple \"target/MintAmazonTagger.dmg\" && fbs upload"
