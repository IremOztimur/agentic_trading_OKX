#!/bin/sh
set -eu

python3 scripts/journal.py init
python3 -m unittest discover -s tests -p 'test_*.py'
echo "Dashboard: http://127.0.0.1:8765"
exec python3 -m http.server 8765 --directory static

