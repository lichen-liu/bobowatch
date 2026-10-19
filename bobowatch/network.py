"""Local IPv4 listener and request-authority policy without extra dependencies."""

from ipaddress import IPv4Address, IPv4Network
from urllib.parse import urlsplit

LOCAL_NETWORKS = tuple(
    IPv4Network(value)
    for value in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
)


def local_ipv4(value):
    try:
        address = IPv4Address(value)
    except (ValueError, TypeError):
        return False
    return any(address in network for network in LOCAL_NETWORKS)


def allowed_authority(value, config):
    if not isinstance(value, str) or not value or len(value) > 300:
        return False
    try:
        parsed = urlsplit("//" + value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            return False
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return False
    if port != config.port:
        return False
    if hostname in ("localhost", "127.0.0.1") or hostname in config.allowed_hosts:
        return True
    if config.host == "0.0.0.0":
        return local_ipv4(hostname)
    return hostname == config.host and local_ipv4(hostname)
