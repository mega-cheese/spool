from __future__ import annotations

import unittest

from src.slack_parser import group_events_by_thread, load_slack_json, parse_slack_json


class SlackParserTest(unittest.TestCase):
    def test_loads_and_groups_fake_slack(self) -> None:
        data = load_slack_json("data/original_fake.json")
        events = parse_slack_json(data)
        grouped = group_events_by_thread(events)

        self.assertEqual(data["workspace"], "AcmeAI")
        self.assertEqual(len(data["channels"]), 3)
        self.assertEqual(len(events), 10)
        self.assertIn("1782201000.000100", grouped)
        self.assertEqual(len(grouped["1782201000.000100"]), 4)


if __name__ == "__main__":
    unittest.main()
