from __future__ import annotations

import unittest

from src.slack_parser import load_slack_json, parse_slack_json


class CanonicalEventTest(unittest.TestCase):
    def test_conversion_preserves_source_fields(self) -> None:
        events = parse_slack_json(load_slack_json("data/original_fake.json"))
        event = events[0]

        self.assertEqual(event.source_type, "slack")
        self.assertEqual(event.workspace, "AcmeAI")
        self.assertEqual(event.channel, "product-strategy")
        self.assertEqual(event.message_ts, "1782201000.000100")
        self.assertEqual(event.speaker, "Maya Chen")
        self.assertIn("delay the enterprise launch", event.text)


if __name__ == "__main__":
    unittest.main()
