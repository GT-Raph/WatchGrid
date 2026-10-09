"""Client IP resolution only trusts the configured number of proxy hops."""


def test_client_ip_honours_only_trusted_proxy_hops(rf, settings):
    from common.utilities.network import client_ip

    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9", REMOTE_ADDR="10.0.0.2")
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 0}
    assert client_ip(request) == "10.0.0.2"  # spoofed header ignored without trusted proxies
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
    assert client_ip(request) == "203.0.113.9"  # the hop our proxy appended
    request = rf.get("/", HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="10.0.0.2")
    assert client_ip(request) is None
