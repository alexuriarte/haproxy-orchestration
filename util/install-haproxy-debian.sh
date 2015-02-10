#!/bin/bash
apt-get update && apt-get install -y haproxy
sed -i "s/ENABLED=0/ENABLED=1/g" /etc/default/haproxy
