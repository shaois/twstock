import unittest

from update_all import FINMIND_URL, fetch_price_rows, resolve_start_index


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"status": 200, "data": [{"date": "2026-09-03"}]}


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class FetchPriceRowsTests(unittest.IsolatedAsyncioTestCase):
    async def test_finmind_token_is_sent_as_bearer_header(self):
        client = RecordingClient()

        rows = await fetch_price_rows(client, "2330", "2026-08-25", "secret-token")

        self.assertEqual(rows, [{"date": "2026-09-03"}])
        self.assertEqual(len(client.calls), 1)
        url, kwargs = client.calls[0]
        self.assertEqual(url, FINMIND_URL)
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secret-token"})
        self.assertNotIn("token", kwargs["params"])


class ResolveStartIndexTests(unittest.TestCase):
    def test_unfinished_cycle_continues_across_taipei_midnight(self):
        progress = {"date": "2026-08-25", "index": 40}

        self.assertEqual(resolve_start_index(progress, "2026-08-26", 200), 40)

    def test_completed_cycle_starts_from_first_stock(self):
        progress = {"date": "2026-08-25", "index": 0}

        self.assertEqual(resolve_start_index(progress, "2026-08-26", 200), 0)

    def test_invalid_progress_starts_from_first_stock(self):
        self.assertEqual(resolve_start_index({}, "2026-08-26", 200), 0)
        self.assertEqual(resolve_start_index({"index": 200}, "2026-08-26", 200), 0)


if __name__ == "__main__":
    unittest.main()
