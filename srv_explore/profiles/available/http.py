"""Профиль curl — только GET/HEAD во внутреннюю сеть. Самодостаточный.
Внешние хосты — только из EXTERNAL_ALLOW. Выключить профиль = отнять агенту сеть.
"""

ID = "http"
COMMANDS = ["curl"]
DESC = "curl (только GET/HEAD, внутренняя сеть; внешнее по allowlist)"

EXTERNAL_ALLOW: list[str] = []

_BOOL = {
    "-s",
    "-S",
    "-v",
    "-i",
    "-I",
    "-L",
    "-k",
    "-g",
    "-4",
    "-6",
    "-#",
    "-N",
    "-f",
    "--silent",
    "--show-error",
    "--verbose",
    "--include",
    "--head",
    "--location",
    "--insecure",
    "--globoff",
    "--compressed",
    "--ipv4",
    "--ipv6",
    "--no-buffer",
    "--fail",
    "--fail-with-body",
    "--http1.0",
    "--http1.1",
    "--http2",
    "--tlsv1.2",
    "--tlsv1.3",
}
_VALUE = {
    "-H",
    "--header",
    "-A",
    "--user-agent",
    "-e",
    "--referer",
    "-b",
    "--cookie",
    "-x",
    "--proxy",
    "-m",
    "--max-time",
    "--connect-timeout",
    "--url",
    "--resolve",
    "--retry",
    "-w",
    "--write-out",
    "--cacert",
    "--cert",
    "--key",
    "--capath",
    "-r",
    "--range",
    "--limit-rate",
    "--dns-servers",
    "--interface",
}


def check(argv, g):
    url = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-X", "--request"):
            if i + 1 >= len(argv) or argv[i + 1].upper() not in ("GET", "HEAD"):
                return False, "curl -X: только GET/HEAD"
            i += 2
            continue
        if a.startswith("--request="):
            if a.split("=", 1)[1].upper() not in ("GET", "HEAD"):
                return False, "curl --request: только GET/HEAD"
            i += 1
            continue
        if a in _VALUE:
            i += 2
            continue
        if a.startswith("--") and "=" in a and a.split("=", 1)[0] in _VALUE:
            i += 1
            continue
        if a in _BOOL:
            i += 1
            continue
        if a.startswith("--"):
            return False, f"curl {a}: флаг не в read-allowlist"
        if a.startswith("-") and len(a) > 1:
            if all(("-" + c) in _BOOL for c in a[1:]):
                i += 1
                continue
            return False, f"curl {a}: флаг не в read-allowlist"
        if url is not None:
            return False, "curl: несколько URL — запрещено"
        url = a
        i += 1
    if not url:
        return False, "curl: нет URL"
    if "://" in url and not url.startswith(("http://", "https://")):
        return False, "curl: только http/https"
    host = g.url_host(url)
    if g.internal_host(host):
        return True, f"curl {host} (внутр., read)"
    if host in EXTERNAL_ALLOW:
        return True, f"curl {host} (allowlist)"
    return False, f"curl {host}: внешний хост вне allowlist — запрещено"
