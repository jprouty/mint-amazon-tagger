#!/bin/bash

cd "$(dirname "$0")/.."

# python3 setup.py block_on_version |
python3 setup.py block_on_version clean sdist bdist_wheel || exit
python3 -m twine upload dist/*
