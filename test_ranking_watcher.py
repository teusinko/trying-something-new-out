import unittest

import ranking_watcher as rw


class RankingParsingTests(unittest.TestCase):
    def test_parse_table_rows(self):
        html = """
        <html><body>
        <table><tbody>
            <tr><td>1</td><td>Alice</td><td>99</td></tr>
            <tr><td>2</td><td>Bob</td><td>88</td></tr>
        </tbody></table>
        </body></html>
        """
        entries = rw.parse_rankings_from_table(html)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].name, "Alice")
        self.assertEqual(entries[1].points, "88")

    def test_parse_embedded_json_rows(self):
        html = """
        <html><body>
        <script>
          window.__DATA__ = {
            "rankings": [
              {"position": 1, "name": "Alice", "points": 99},
              {"position": 2, "name": "Bob", "points": 88}
            ]
          };
        </script>
        </body></html>
        """
        entries = rw.parse_rankings_from_embedded_json(html)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].position, "1")
        self.assertEqual(entries[1].name, "Bob")

    def test_normalize_entries_ignores_timestamp(self):
        entries = [
            rw.RankingEntry(position="1", name="A", points="10"),
            rw.RankingEntry(position="2", name="B", points="9"),
        ]
        n1 = rw.normalize_entries(entries)
        n2 = rw.normalize_entries(entries)
        self.assertEqual(n1, n2)


if __name__ == "__main__":
    unittest.main()
