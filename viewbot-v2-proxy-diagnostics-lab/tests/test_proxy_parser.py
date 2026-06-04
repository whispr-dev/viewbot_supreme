from viewbot_lab.proxy_parser import parse_proxy_line, parse_proxy_text


def test_schemed_proxy_parse():
    entries = parse_proxy_line("socks5://127.0.0.1:1080")
    assert len(entries) == 1
    assert entries[0].url == "socks5://127.0.0.1:1080"


def test_bare_proxy_expands():
    entries = parse_proxy_line("127.0.0.1:8080", missing_scheme_mode="expand")
    assert [entry.url for entry in entries] == [
        "http://127.0.0.1:8080",
        "https://127.0.0.1:8080",
        "socks4://127.0.0.1:8080",
        "socks5://127.0.0.1:8080",
    ]


def test_dedupe():
    report = parse_proxy_text("http://127.0.0.1:8080\nhttp://127.0.0.1:8080\n")
    assert len(report.entries) == 1
    assert report.deduped == 1
