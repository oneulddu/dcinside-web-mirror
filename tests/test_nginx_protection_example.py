from pathlib import Path


CONFIG_PATH = Path(__file__).parents[1] / "ops" / "nginx" / "mirror-protection.conf.example"


def test_nginx_protection_example_covers_ai_crawlers_and_read_rate_limit():
    config = CONFIG_PATH.read_text(encoding="utf-8")

    for user_agent in ("ClaudeBot", "Claude-SearchBot", "Claude-User", "GPTBot"):
        assert user_agent in config
    assert "limit_req_zone $binary_remote_addr zone=mirror_read_per_ip:10m rate=2r/s;" in config
    assert "location = /read" in config
    assert "limit_req zone=mirror_read_per_ip burst=10 nodelay;" in config
    assert "/var/log/nginx/blocked-ai-crawlers.log" in config
