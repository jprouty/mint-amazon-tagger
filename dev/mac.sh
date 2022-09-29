#!/bin/bash
# A script that creates, updates, and activates a virtual environment
# specifically for development.
# Must call this script via:
#   source dev/mac.sh

if [ -d "dev_venv" ]
then
    source dev_venv/bin/activate
    pip install --upgrade -r requirements/base.txt -r requirements/mac.txt -r requirements/dev.txt
else
    python3 -m venv dev_venv
    source dev_venv/bin/activate
    pip install --upgrade -r requirements/base.txt -r requirements/mac.txt -r requirements/dev.txt
fi
