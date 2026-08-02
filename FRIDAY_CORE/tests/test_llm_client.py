# tests/test_llm_client.py
"""Reachability fallback: resolve_host and the cache wrapped around it.

resolve_host is a plain function of (configured, probe) precisely so this
file can drive it with a fake probe — no network, no ollama package needed,
which matters because CI does not install ollama (see
tests/test_imports_without_runtime_deps.py). _resolved_host is exercised too,
but only through that same fake-probe seam; nothing here calls get_client()
or constructs a real ollama.Client, which would require the package this
suite deliberately runs without.
"""
from core import llm_client
from core.llm_client import LOCALHOST, resolve_host


def test_configured_localhost_is_trusted_without_probing():
    probed = []
    host, fell_back = resolve_host(LOCALHOST, lambda h: probed.append(h) or True)

    assert host == LOCALHOST
    assert fell_back is False
    assert probed == []  # the round trip is skipped entirely for the default


def test_reachable_non_local_host_is_used_as_configured():
    host, fell_back = resolve_host("http://192.168.1.50:11434", lambda h: True)

    assert host == "http://192.168.1.50:11434"
    assert fell_back is False


def test_unreachable_non_local_host_falls_back_to_localhost():
    # The case that matters most: a Pi 5 that has dropped off wifi, or (per
    # the operator's own verification instruction) a port nothing is
    # listening on at all, e.g. http://127.0.0.1:59999.
    host, fell_back = resolve_host("http://127.0.0.1:59999", lambda h: False)

    assert host == LOCALHOST
    assert fell_back is True


def test_a_raising_probe_counts_as_unreachable_not_as_a_crash():
    def _probe(host):
        raise ConnectionError("no route to host")

    host, fell_back = resolve_host("http://10.0.0.5:11434", _probe)

    assert host == LOCALHOST
    assert fell_back is True


def test_resolved_host_is_cached_so_the_probe_runs_once_per_configured_host(monkeypatch):
    llm_client._resolved_host.cache_clear()
    calls = []

    def _fake_probe(host):
        calls.append(host)
        return False

    monkeypatch.setattr(llm_client, "_probe_reachable", _fake_probe)

    first = llm_client._resolved_host("http://10.0.0.5:11434")
    second = llm_client._resolved_host("http://10.0.0.5:11434")

    assert first == second == LOCALHOST
    assert calls == ["http://10.0.0.5:11434"]  # not called again on the second lookup
