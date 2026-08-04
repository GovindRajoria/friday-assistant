# skills/utility/network_status.py
"""Is the network up, what address is this machine on, and how far away is a host.

Overlaps §1's geolocation deliberately and stops short of it: this reports the
*local* addresses and whether the internet is reachable. It does not ask a
third-party service where the machine is, because that is an outbound request that
reveals an IP and belongs behind its own setting.

Reachability is a TCP connect to a well-known port rather than ICMP, for two
reasons: ping needs elevation on some configurations, and plenty of networks drop
ICMP while passing TCP perfectly well — so a ping-based check reports an outage
that is not there.

Terminal: the report is the answer.
"""
import socket
import time

# Two, so one being down does not read as "the internet is down".
PROBES = (("1.1.1.1", 443, "Cloudflare"), ("8.8.8.8", 53, "Google DNS"))
PROBE_TIMEOUT_SECONDS = 3.0


class NetworkStatusSkill:
    def __init__(self):
        self.manifest = {
            "name": "network_status",
            "description": (
                "Reports whether this machine has working network access, its local IP "
                "addresses and hostname, and the round-trip time to a host. Parameters: "
                "optionally 'host' to measure latency to something specific. Use this when "
                "asked if the internet is working, what this machine's IP is, or whether a "
                "server is reachable. Its answer is complete — the turn ends when it returns. "
                "Use check_camera_stream for a specific camera, and diagnose_self when the "
                "question is whether everything is working rather than the network alone."
            ),
            "parameters": ["host"],
            "terminal": True,
        }

    def execute(self, params=None):
        params = params or {}
        lines = [self._local(), self._internet()]

        host = str(params.get("host") or "").strip()
        if host:
            lines.append(self._latency(host))

        return {
            "status": "success",
            "message": "\n".join(lines),
            "data": {"checked_host": host or None},
        }

    def _local(self):
        hostname = socket.gethostname()
        addresses = self._local_addresses()
        rendered = ", ".join(addresses) if addresses else "no non-loopback address"
        return f"This machine is '{hostname}' at {rendered}."

    @staticmethod
    def _local_addresses():
        """Addresses actually routable from here.

        The UDP-connect trick rather than gethostbyname: on a machine with a VPN,
        a virtual switch and a docker bridge, name resolution returns whichever
        one it feels like, while connecting a datagram socket reveals the address
        the OS would really use to reach that destination. No packet is sent.
        """
        found = []
        for target in ("1.1.1.1", "8.8.8.8"):
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.settimeout(0.5)
                probe.connect((target, 80))
                address = probe.getsockname()[0]
                if address and not address.startswith("127.") and address not in found:
                    found.append(address)
            except OSError:
                continue
            finally:
                probe.close()
        return found

    def _internet(self):
        results = []
        for host, port, label in PROBES:
            elapsed = self._connect_ms(host, port)
            results.append((label, elapsed))

        reachable = [(label, ms) for label, ms in results if ms is not None]
        if not reachable:
            return ("No outbound connection: neither " +
                    " nor ".join(label for _host, _port, label in PROBES) +
                    " answered. Anything needing the internet will fail; a local model "
                    "still works.")
        rendered = ", ".join(f"{label} {ms:.0f} ms" for label, ms in reachable)
        if len(reachable) < len(results):
            unreachable = ", ".join(label for label, ms in results if ms is None)
            return f"Internet is up ({rendered}), though {unreachable} did not answer."
        return f"Internet is up: {rendered}."

    def _latency(self, host):
        target, _, port_text = host.partition(":")
        try:
            port = int(port_text) if port_text else 443
        except ValueError:
            port = 443

        try:
            resolved = socket.gethostbyname(target)
        except socket.gaierror:
            return f"'{target}' does not resolve, so it cannot be reached by name."

        samples = [self._connect_ms(resolved, port) for _ in range(3)]
        good = [ms for ms in samples if ms is not None]
        if not good:
            return (f"{target} ({resolved}) resolves but nothing accepted a connection on "
                    f"port {port}.")
        return (f"{target} ({resolved}) on port {port}: "
                f"{min(good):.0f}-{max(good):.0f} ms over {len(good)} of 3 attempts.")

    @staticmethod
    def _connect_ms(host, port):
        """Milliseconds to complete a TCP handshake, or None."""
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
                return (time.perf_counter() - started) * 1000
        except OSError:
            return None


def setup():
    return NetworkStatusSkill()
