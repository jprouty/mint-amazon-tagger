#!/bin/bash

python3 block_stale_release.py || exit
python3 setup.py clean sdist bdist_wheel
python3 -m twine upload dist/*
