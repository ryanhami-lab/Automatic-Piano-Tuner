# Launch the existing local environment. No installation or policy changes.
$ErrorActionPreference = 'Stop'
$tunerPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $tunerPython -PathType Leaf)) {
    throw 'Create the .venv environment and install requirements/host.lock.txt as described in README.md.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $tunerPython -m pianotuner @args
    $tunerExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $tunerExit
