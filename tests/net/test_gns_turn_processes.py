"""TURN process verdicts must not assume one ICE allocation per peer."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import gns_turn_processes as turn


class TurnProcessVerdicts(unittest.TestCase):
    def run_case(self, mode, count, permission_requests=7, refreshes=6, stale_nonce_replies=36):
        server = Mock()
        server.port = 27020
        server.stale_nonce_replies = stale_nonce_replies
        server.statistics.return_value = [dict(permission_requests=permission_requests, refreshes=refreshes)
                                          for _ in range(count)]
        broker = Mock(returncode=0, stdout=io.StringIO("27021\n"))
        peer = Mock(returncode=0)
        for child in (broker, peer):
            child.communicate.return_value = ("", None)
            child.poll.return_value = 0
        with patch.object(turn, "lan_address", return_value="192.0.2.1"), \
                patch.object(turn, "TurnServer", return_value=server), \
                patch.object(turn.subprocess, "Popen", side_effect=[broker, peer, peer]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return turn.run_case("unused-test-executable", mode)

    def test_two_peers_may_refresh_multiple_interface_candidates(self):
        for mode in ("permission", "nonce"):
            for count in (2, 6):
                with self.subTest(mode=mode, allocations=count):
                    self.assertEqual(self.run_case(mode, count), 0)

    def test_at_least_two_allocations_must_repeatedly_refresh_permissions(self):
        for mode in ("permission", "nonce"):
            for count in (0, 1):
                with self.subTest(mode=mode, allocations=count):
                    self.assertEqual(self.run_case(mode, count), 1)
            self.assertEqual(self.run_case(mode, 6, permission_requests=2), 1)

    def test_nonce_case_still_requires_repeated_refresh_and_stale_replies(self):
        self.assertEqual(self.run_case("nonce", 6, refreshes=2), 1)
        self.assertEqual(self.run_case("nonce", 6, stale_nonce_replies=5), 1)


if __name__ == "__main__":
    unittest.main()
