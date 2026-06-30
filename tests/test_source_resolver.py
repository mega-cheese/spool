from __future__ import annotations

import unittest

from src.source_resolver import InvalidSourceError, build_event_lookup, resolve_source
from src.slack_parser import load_slack_json, parse_slack_json


class SourceResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        events = parse_slack_json(load_slack_json("data/original_fake.json"))
        self.lookup = build_event_lookup(events)

    def test_resolves_valid_source(self) -> None:
        source = resolve_source("1782201060.000200", self.lookup)
        self.assertEqual(source.speaker, "Leo Park")
        self.assertIn("audit logs and SSO", source.quote)

    def test_rejects_invalid_source(self) -> None:
        with self.assertRaises(InvalidSourceError):
            resolve_source("missing", self.lookup)


if __name__ == "__main__":
    unittest.main()
