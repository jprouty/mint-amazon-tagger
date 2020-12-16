#!/bin/bash

$scriptpath = $MyInvocation.MyCommand.Path
$dir = Split-Path $scriptpath
Push-Location $dir\..

python setup.py clean

echo "Setup the release venv"
python -m venv release_venv
.\release_venv\Scripts\activate.ps1
pip install --upgrade pip
pip install --upgrade -r requirements/base.txt -r requirements/windows.txt

echo "Build it"
# --icon .\icons\base\32.ico `
pyinstaller `
  --name="MintAmazonTagger" `
  --onefile `
  --windowed `
  --icon .\icons\Icon.ico `
  .\mintamazontagger\main.py

echo "Signing the app"
$password = Get-Content .\sign\password -Raw
signtool sign `
  /f .\sign\certificate.pfx `
  /p $password.trim() `
  /d "Mint Amazon tagger matches amazon purchases with your mint transactions, giving them useful descriptions."  `
  /du "https://github.com/jprouty/mint-amazon-tagger" `
  .\dist\MintAmazonTagger.exe

signtool sign `
  /f .\sign\certificate.pfx `
  /p $password.trim() `
  /d "Mint Amazon tagger matches amazon purchases with your mint transactions, giving them useful descriptions."  `
  /du "https://github.com/jprouty/mint-amazon-tagger" `
  /as /fd sha256 /td sha256  `
  .\dist\MintAmazonTagger.exe

echo "Now verify the built version works"
.\dist\MintAmazonTagger.exe | Out-Null

deactivate
Remove-Item .\release_venv\ -recurse
