#!/bin/bash

# exit when any command fails
set -e

cd "$(dirname "$0")/.."

# python3 setup.py block_on_version |
python3 setup.py block_on_version clean sdist bdist_wheel || exit

# Publish.
python3 -m twine upload --repository mint-amazon-tagger dist/*

# Verify the package is installable in a virtual env.
python3 -m venv pypi_test_venv
source pypi_test_venv/bin/activate

pip install --upgrade pip
# Wait 180 seconds for pypi before attempting to install the newly published version.
sleep 180
pip install --no-cache-dir mint-amazon-tagger

# Get out of the root directory so the live src version isn't used when verifying the pypi module.
cd pypi_test_venv
python -m mintamazontagger.main
cd ..

deactivate
rm -rf pypi_test_venv
    