"""Tests for the in-memory rate limiter."""

import time
import pytest
from unittest.mock import patch

from app.auth.middleware import RateLimiter


class TestRateLimiter:
    def test_allows_up_to_limit(self):
        rl = RateLimiter(per_user_limit=5, per_ip_limit=10, window_seconds=60)
        for _ in range(5):
            assert rl.check_user("user1") is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(per_user_limit=3, per_ip_limit=10, window_seconds=60)
        for _ in range(3):
            rl.check_user("user1")
        assert rl.check_user("user1") is False

    def test_ip_and_user_counts_are_independent(self):
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=60)
        # user1 exhausts user limit
        rl.check_user("user1")
        rl.check_user("user1")
        assert rl.check_user("user1") is False
        # IP has separate bucket — still allowed
        assert rl.check_ip("10.0.0.1") is True

    def test_different_users_independent(self):
        rl = RateLimiter(per_user_limit=1, per_ip_limit=100, window_seconds=60)
        assert rl.check_user("alice") is True
        assert rl.check_user("alice") is False
        assert rl.check_user("bob") is True  # separate bucket

    def test_window_resets_after_expiry(self):
        rl = RateLimiter(per_user_limit=2, per_ip_limit=100, window_seconds=1)
        rl.check_user("u")
        rl.check_user("u")
        assert rl.check_user("u") is False

        # Simulate window expiry by manually backdating the timestamps
        rl._counts["u:u"] = [time.time() - 2]  # 2 seconds ago, outside 1-second window
        assert rl.check_user("u") is True

    def test_ip_per_ip_limit(self):
        rl = RateLimiter(per_user_limit=100, per_ip_limit=3, window_seconds=60)
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is True
        assert rl.check_ip("1.2.3.4") is False

    def test_evicts_expired_entries_on_check(self):
        rl = RateLimiter(per_user_limit=3, per_ip_limit=10, window_seconds=1)
        # Add 2 old entries manually
        rl._counts["u:user"] = [time.time() - 2, time.time() - 2]
        # Should be evicted, freeing budget — all 3 checks pass
        assert rl.check_user("user") is True
        assert rl.check_user("user") is True
        assert rl.check_user("user") is True
        assert rl.check_user("user") is False  # 4th exceeds limit
