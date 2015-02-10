#!/usr/bin/env python
import sys
import os
import json
import shutil
import subprocess
import StringIO

# This script re-creates a HAproxy configuration, and reloads HAproxy
# accordingly.

# Configuration is expected in JSON format in a Global Variable called
# HAPROXY_CONFIGURATION. The configuration format is as follows:

# [
#  {
#    "name": "my-app",
#    "listen": {
#      "bind": "*",
#      "port": 80
#    },
#    "upstream": {
#      "alias": "my-app-role"
#      "port": 80
#    }
#  }
# ]

HAPROXY_CONFIGURATION_FILE = "/etc/haproxy/haproxy.cfg"

BASE_CONFIGURATION = """
global
  log /dev/log local0
  log /dev/log local1 notice
  chroot /var/lib/haproxy
  user haproxy
  group haproxy

defaults
  log  global
  mode http
  option httplog
  option dontlognull
  contimeout 5000
  clitimeout 50000
  srvtimeout 50000
  errorfile 400 /etc/haproxy/errors/400.http
  errorfile 403 /etc/haproxy/errors/403.http
  errorfile 408 /etc/haproxy/errors/408.http
  errorfile 500 /etc/haproxy/errors/500.http
  errorfile 502 /etc/haproxy/errors/502.http
  errorfile 503 /etc/haproxy/errors/503.http
  errorfile 504 /etc/haproxy/errors/504.http
"""


def main():
    config = os.environ.get("HAPROXY_CONFIGURATION")

    if config is None:
        raise Exception("No HAproxy configuration!")

    config = json.loads(config)

    # Resulting configuration file
    s = StringIO.StringIO()
    s.write(BASE_CONFIGURATION)
    s.write("\n")

    must_reload = False

    print "Loading from queryenv"
    p = subprocess.Popen(["szradm", "queryenv", "--format=json", "list-roles"], stdout=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode:
        raise Exception("Failed to get servers from szradm: {0}".format(stderr))
    queryenv = json.loads(out)

    for proxy_config in config:
        print "Processing: {0}".format(proxy_config["name"])

        # Start with getting a list of servers to proxy to.
        alias = proxy_config["upstream"]["alias"]

        upstream_role = [role for role in queryenv["roles"]
                        if role["alias"] == proxy_config["upstream"]["alias"]]

        # Check we have a Farm Role that matches the configuration
        if not upstream_role:
            print "No role for: {0}".format(proxy_config["upstream"]["alias"])
            continue
        upstream_role, = upstream_role

        # Check we have some Servers in running state in that Farm Role
        servers = [server for server in upstream_role["hosts"] if server["status"] == "Running"]
        if not servers:
            print "No running upstream servers for: {0}".format(proxy_config["upstream"]["alias"])
            continue

        # All good, create the configuration

        # Frontend
        s.write("frontend {0}-in\n".format(proxy_config["name"]))
        s.write("  bind {0}:{1}\n".format(proxy_config["listen"]["bind"], proxy_config["listen"]["port"]))
        s.write("  default_backend {0}-out\n".format(proxy_config["name"]))

        # Backend
        s.write("backend {0}-out\n".format(proxy_config["name"]))
        for server in servers:
            s.write("  server {0}-{1} {2}:{3}\n".format(proxy_config["name"], server["index"], server["internal-ip"], proxy_config["upstream"]["port"]))

        must_reload = True

    if not must_reload:
        print "Nothing to do, exiting."
        return

    # Note: consider making a backup first
    s.seek(0)
    with open(HAPROXY_CONFIGURATION_FILE, 'w') as f:
        shutil.copyfileobj(s, f)

    # Check configuration
    subprocess.check_call(["haproxy", "-f", HAPROXY_CONFIGURATION_FILE, "-c"])

    # Identify whether Haproxy should be started or reloaded
    cmd = "reload" if subprocess.call(["service", "haproxy", "status"]) == 0 else "start"

    print "Reloading haproxy with: {0}".format(cmd)
    subprocess.check_call(["service", "haproxy", cmd])

if __name__ == "__main__":
    main()

