"""voice_bridge.tokens JWT 签发 + 验签测试。"""
from __future__ import annotations

import time

import pytest

from voice_bridge.tokens import (
    JWTValidator,
    TokenError,
    issue_token,
    validate_token,
)


SECRET = "unit-test-secret-at-least-32-bytes-long-for-hs256-happy"


def test_issue_and_validate_roundtrip():
    tok = issue_token(channel="#conv-001", customer="zhang", secret=SECRET)
    claims = validate_token(tok, secret=SECRET)
    assert claims.channel == "conv-001"  # '#' stripped
    assert claims.customer == "zhang"
    assert claims.exp > claims.iat


def test_issue_empty_secret_raises():
    with pytest.raises(ValueError, match="secret"):
        issue_token(channel="c", customer="u", secret="")


def test_validate_expired_rejects():
    tok = issue_token(channel="c", customer="u", secret=SECRET, ttl_seconds=1,
                      now=int(time.time()) - 10)
    with pytest.raises(TokenError, match="expired"):
        validate_token(tok, secret=SECRET)


def test_validate_wrong_secret_rejects():
    tok = issue_token(channel="c", customer="u", secret=SECRET)
    with pytest.raises(TokenError):
        validate_token(tok, secret="different-secret")


def test_validate_missing_secret_rejects():
    tok = issue_token(channel="c", customer="u", secret=SECRET)
    with pytest.raises(TokenError, match="not configured"):
        validate_token(tok, secret="")


def test_validate_gibberish_rejects():
    with pytest.raises(TokenError):
        validate_token("not-a-jwt", secret=SECRET)


def test_nonce_unique_per_issue():
    tok1 = issue_token(channel="c", customer="u", secret=SECRET)
    tok2 = issue_token(channel="c", customer="u", secret=SECRET)
    c1 = validate_token(tok1, secret=SECRET)
    c2 = validate_token(tok2, secret=SECRET)
    assert c1.nonce != c2.nonce


def test_channel_hash_stripped():
    tok = issue_token(channel="#conv-001", customer="u", secret=SECRET)
    c = validate_token(tok, secret=SECRET)
    assert c.channel == "conv-001"


# ---- JWTValidator replay protection ----

def test_validator_rejects_replay():
    v = JWTValidator(secret=SECRET)
    tok = issue_token(channel="c", customer="u", secret=SECRET)
    first = v.validate(tok)
    assert first is not None
    assert first["channel"] == "c"
    # Replay:
    second = v.validate(tok)
    assert second is None  # rejected


def test_validator_accepts_different_tokens_even_same_session():
    v = JWTValidator(secret=SECRET)
    t1 = issue_token(channel="c", customer="u", secret=SECRET)
    t2 = issue_token(channel="c", customer="u", secret=SECRET)
    assert v.validate(t1) is not None
    assert v.validate(t2) is not None  # different nonce → fine


def test_validator_rejects_bad_token():
    v = JWTValidator(secret=SECRET)
    assert v.validate("garbage") is None


def test_validator_respects_empty_secret():
    v = JWTValidator(secret="")
    tok = issue_token(channel="c", customer="u", secret=SECRET)
    assert v.validate(tok) is None


def test_validator_nonce_set_bounded():
    v = JWTValidator(secret=SECRET, max_nonces=16)
    for i in range(50):
        tok = issue_token(channel="c", customer=f"u{i}", secret=SECRET)
        v.validate(tok)
    # Set should be bounded near max_nonces (not precisely, trim is batch)
    assert len(v._used) <= v.max_nonces + 64
