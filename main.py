#!/usr/bin/env python
import sys
import os
import json
import shutil
import subprocess
import StringIO
import logging


logger = logging.getLogger("haproxy")

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

CONFIG_ENVIRON = "HAPROXY_CONFIGURATION"
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
  option forwardfor
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
    config = os.environ.get(CONFIG_ENVIRON)

    if config is None:
        logger.error("No HAproxy configuration in the environment; provide one in %s", CONFIG_ENVIRON)
        sys.exit(-1)

    try:
        config = json.loads(config)
    except ValueError:
        logger.exception("HAproxy configuration is invalid")
        sys.exit(-1)

    # Stream for the resulting configuration file
    s = StringIO.StringIO()
    s.write(BASE_CONFIGURATION)
    s.write("\n")

    # Whether we found anything to do.
    must_reload = False

    # Load the rest of the Farm from queryenv
    logger.info("Loading Farm from queryenv")

    p = subprocess.Popen(["szradm", "queryenv", "--format=json", "list-roles"], stdout=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode:
        logger.exception("Got a error from szradm: %s", stderr)
        sys.exit(1)
    queryenv = json.loads(out)

    for proxy_config in config:
        logger.info("Processing: %s", proxy_config["name"])
        proxy_logger = logging.getLogger("haproxy.{0}".format(proxy_config["name"]))

        # Start with getting a list of servers to proxy to.
        alias = proxy_config["upstream"]["alias"]

        upstream_role = [role for role in queryenv["roles"]
                        if role["alias"] == proxy_config["upstream"]["alias"]]

        # Check we have a Farm Role that matches the configuration
        if not upstream_role:
            proxy_logger.warning("Upstream Farm Role '%s' was not found", proxy_config["upstream"]["alias"])
            continue
        upstream_role, = upstream_role
        proxy_logger.debug("Found upstream Farm Role '%s': #%s", upstream_role["alias"], upstream_role["id"])

        # Check we have some Servers in running state in that Farm Role
        servers = [server for server in upstream_role["hosts"] if server["status"] == "Running"]
        if not servers:
            proxy_logger.warning("No running upstream server found")
            continue
        proxy_logger.debug("Found %s running upstream servers", len(servers))

        # All good, create the configuration

        # Frontend
        s.write("frontend {0}-in\n".format(proxy_config["name"]))
        s.write("  bind {0}:{1}\n".format(proxy_config["listen"]["bind"], proxy_config["listen"]["port"]))
        s.write("  default_backend {0}-out\n".format(proxy_config["name"]))

        # Backend
        s.write("backend {0}-out\n".format(proxy_config["name"]))
        for server in servers:
            proxy_logger.debug("Adding upstream server: %s", server["internal-ip"])
            s.write("  server {0}-{1} {2}:{3}\n".format(proxy_config["name"], server["index"], server["internal-ip"], proxy_config["upstream"]["port"]))

        must_reload = True

    if not must_reload:
        logger.info("Nothing to do, exiting")
        sys.exit(0)

    # Note: consider making a backup first
    s.seek(0)
    with open(HAPROXY_CONFIGURATION_FILE, 'w') as f:
        shutil.copyfileobj(s, f)

    # Check configuration
    logger.info("Checking generated haproxy configuration is valid")
    subprocess.check_call(["haproxy", "-f", HAPROXY_CONFIGURATION_FILE, "-c"])

    # Identify whether Haproxy should be started or reloaded
    logger.info("Reloading haproxy")
    cmd = "reload" if subprocess.call(["service", "haproxy", "status"]) == 0 else "start"

    logger.debug("Reloading with command: %s", cmd)
    subprocess.check_call(["service", "haproxy", cmd])

if __name__ == "__main__":
    logging.basicConfig(format="[%(asctime)s: %(levelname)s/%(name)s] %(message)s", level=logging.DEBUG)
    main()

