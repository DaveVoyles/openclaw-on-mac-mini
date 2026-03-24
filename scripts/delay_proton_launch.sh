#!/bin/bash

# Configuration
DELAY_SECONDS=30
CHECK_INTERVAL=5
MAX_ATTEMPTS=20
TARGET_HOST="8.8.8.8"

echo "Waiting $DELAY_SECONDS seconds before checking internet connection..."
sleep $DELAY_SECONDS

echo "Checking for internet connection to $TARGET_HOST..."
attempts=0
until ping -c 1 -W 5 $TARGET_HOST > /dev/null 2>&1 || [ $attempts -eq $MAX_ATTEMPTS ]; do
    echo "No connection yet. Waiting $CHECK_INTERVAL seconds..."
    sleep $CHECK_INTERVAL
    ((attempts++))
done

if [ $attempts -eq $MAX_ATTEMPTS ]; then
    echo "Max attempts reached. Internet may still be down."
else
    echo "Internet connection detected. Launching Proton VPN..."
fi

# Launch Proton VPN
open -a "ProtonVPN"
