"""Credential scanning should recognize tokens without flagging URL word endings."""

from scripts.scan_secrets import PATTERNS


def test_token_boundaries():
    token = "sk-" + "a" * 24
    assert PATTERNS[0].search(f'key="{token}"')
    assert not PATTERNS[0].search("https://example.com/mask-" + "a" * 24)
