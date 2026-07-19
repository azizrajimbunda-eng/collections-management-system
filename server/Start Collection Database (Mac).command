#!/bin/bash
# Double-click this to start the Collection Database on a Mac.
cd "$(dirname "$0")"
echo "Starting the Collection Database... a browser window will open shortly."
python3 run_server.py
echo ""
echo "The server has stopped. You can close this window."
