#!/bin/bash

# exit when any command fails
set -e

cd "$(dirname "$0")/.."

# python3 setup.py block_on_version |
python setup.py block_on_version clean sdist bdist_wheel || exit

# Publish to max_days_between_payment_and_shipping.
python -m twine upload dist/*

# Verify the package is installable in a virtual env.
python -m venv pypi_test_venv
source pypi_test_venv/bin/activate

pip install --upgrade pip
pip install --no-cache-dir mint-amazon-tagger

# Get out of the root directory so the live src version isn't used when verifying the pypi module.
cd pypi_test_venv
python -m mintamazontagger.main
cd ..

deactivate
rm -rf pypi_test_venv
