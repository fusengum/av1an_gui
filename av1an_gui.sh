#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh
exec .venv/bin/python -m av1gui "$@"
