from pathlib import Path
import re


CONFIG_PATH = Path(__file__).parents[1] / "ops" / "nginx" / "mirror-protection.conf.example"


def test_nginx_protection_example_covers_ai_crawlers_and_read_rate_limit():
    config = CONFIG_PATH.read_text(encoding="utf-8")

    for user_agent in ("ClaudeBot", "Claude-SearchBot", "Claude-User", "GPTBot"):
        assert user_agent in config
    assert "limit_req_zone $binary_remote_addr zone=mirror_read_per_ip:10m rate=2r/s;" in config
    assert "location = /read" in config
    assert "limit_req zone=mirror_read_per_ip burst=10 nodelay;" in config
    assert "/var/log/nginx/blocked-ai-crawlers.log" in config


def test_write_upload_limits_and_proxy_headers():
    config = CONFIG_PATH.read_text(encoding="utf-8")
    block = re.search(r"location = /write\s*\{([^}]+)\}", config).group(1)
    assert "client_max_body_size 32m;" in block
    assert "proxy_read_timeout 60s;" in block
    assert "proxy_pass http://127.0.0.1:6100;" in block
    for header, value in [("Host", "$host"), ("X-Real-IP", "$remote_addr"),
                          ("X-Forwarded-For", "$proxy_add_x_forwarded_for"),
                          ("X-Forwarded-Proto", "$scheme")]:
        assert re.search(rf"proxy_set_header\s+{header}\s+{re.escape(value)};", block)
