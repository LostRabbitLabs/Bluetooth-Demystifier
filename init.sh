#!/bin/bash
device=`hcitool dev | awk '$0=$2'`
rm log.txt
rm -rf /var/lib/bluetooth/$device/*
sudo pkill python3
sudo service bluetooth restart
sudo python3 server.py
