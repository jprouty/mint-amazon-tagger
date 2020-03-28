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

    echo "Creating installer"
    fbs installer
elif [[ "$OSTYPE" == darwin* ]]; then
    target/MintAmazonTagger.app/Contents/MacOS/MintAmazonTagger

    echo "Signing the app"
    APP_IDENTITY="Developer ID Application: Jeff Prouty (NRC455QXS5)"
    EMAIL="jeff.prouty@gmail.com"

    codesign --verify --verbose --force --deep --sign "${APP_IDENTITY}" --entitlements entitlements.plist --options=runtime target/MintAmazonTagger.app

    echo "Creating installer"
    fbs installer

    echo "Signing installer"
    codesign --verify --verbose --force --deep --sign "${APP_IDENTITY}" --entitlements entitlements.plist --options=runtime target/MintAmazonTagger.dmg

    echo "Creating an Apple notary request"
    xcrun altool --notarize-app \
        --primary-bundle-id "com.jeffprouty.mintamazontagger" \
        --username "${EMAIL}" \
        --password "@keychain:AC_PASSWORD" \
        --file target/MintAmazonTagger.dmg

    echo "Check notary status later via: "
    echo "xcrun altool --notarization-history 0 -u \"${EMAIL}\" -p \"@keychain:AC_PASSWORD\""
    echo "  AND"
    echo "xcrun altool --notarization-info <REQUEST_ID> -u \"${EMAIL}\""
    echo
    echo "Once successful, staple and re-upload:"
    echo "xcrun stapler staple \"target/MintAmazonTagger.dmg\" && fbs upload"
fi

echo "Uploading installer"
fbs upload

deactivate

rm -rf release_venv
