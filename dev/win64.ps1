# A script that creates, updates, and activates a virtual environment
# specifically for development.
# Must call this script via:
#   source dev/win64.ps1

if (Test-Path -Path 'dev_venv') {
    .\dev_venv\Scripts\activate.ps1
    pip install --upgrade -r requirements/base.txt -r requirements/windows.txt -r requirements/dev.txt
} else {
    python -m venv dev_venv
    .\dev_venv\Scripts\activate.ps1
    pip install --upgrade -r requirements/base.txt -r requirements/windows.txt -r requirements/dev.txt
}
