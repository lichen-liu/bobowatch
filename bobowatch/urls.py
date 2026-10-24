"""Dashboard links from the configured listener and local interface inventory."""

import re
import subprocess
import sys
from ipaddress import IPv4Address

from .network import local_ipv4


def lan_interfaces():
    """Best-effort macOS discovery; no DNS lookup or external network probe."""
    if sys.platform != "darwin":
        return []
    try:
        result = subprocess.run(["/sbin/ifconfig", "-a"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    addresses = []
    seen = set()
    for block in re.split(r"(?=^\S+: flags=)", result.stdout, flags=re.MULTILINE):
        header = re.match(r"([^\s:]+): flags=\d+<([^>]*)>", block)
        if not header or "UP" not in header[2].split(","):
            continue
        if re.search(r"^\s*status: inactive\s*$", block, flags=re.MULTILINE):
            continue
        for address in re.findall(r"^\s*inet (\S+)", block, flags=re.MULTILINE):
            if local_ipv4(address) and not IPv4Address(address).is_loopback and address not in seen:
                addresses.append({"interface": header[1], "address": address})
                seen.add(address)
    return addresses


def dashboard_urls(config):
    """Only advertise authorities accepted by the existing listener policy."""
    result = {"local": f"http://127.0.0.1:{config.port}/", "lan": []}
    if config.host != "0.0.0.0":
        result["lan_note"] = "LAN access is disabled: the listener is loopback-only."
        return result
    result["lan"] = [
        {"interface": item["interface"], "url": f"http://{item['address']}:{config.port}/"}
        for item in lan_interfaces()
    ]
    if config.allowed_hosts:
        result["hostnames"] = [f"http://{host}:{config.port}/" for host in config.allowed_hosts]
    if not result["lan"]:
        result["lan_note"] = (
            f"No LAN IPv4 address detected; use this Mac's LAN IP with port {config.port}."
        )
    return result


def print_dashboard(config):
    links = dashboard_urls(config)
    print("Dashboard (this Mac): " + links["local"], flush=True)
    for item in links["lan"]:
        print(f"Dashboard (local network, {item['interface']}): {item['url']}", flush=True)
    for url in links.get("hostnames", []):
        print("Dashboard (configured hostname): " + url, flush=True)
    if "lan_note" in links:
        print(links["lan_note"], flush=True)
