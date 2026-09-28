#!/bin/sh
# Launch the existing local environment. No installation or system changes.
set -eu
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
    printf '%s\n' 'Create the .venv environment and install requirements/host.lock.txt as described in README.md.' >&2
    exit 2
fi
exec .venv/bin/python -m pianotuner "$@"
