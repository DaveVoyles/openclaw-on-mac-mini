#!/bin/bash

# Delay for 30 seconds
sleep 30

# Check for internet connection (google.com)
while ! ping -c 1 -n -w 1 8.8.8.8 &> /dev/null; do
    echo "Waiting for internet connection..."
    sleep 5
done

# Launch Proton VPN
open -a "ProtonVPN"
