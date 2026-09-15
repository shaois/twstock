"""Offline proxy tests: never call providers or use real credentials."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
import main


class AIErrors(unittest.TestCase):
    def request(self, provider="groq"):
        return {"api_key": "gsk_TESTSECRET123", "body": {
            "model": "llama-3.3-70b-versatile" if provider == "groq" else "meta/llama-3.3-70b-instruct",
            "messages": [{"role": "user", "content": "private prompt content"}]}}

    def invoke(self, response, provider="groq"):
        calls = []
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, *args, **kwargs):
                calls.append(1)
                return response
        with patch.object(main.httpx, "AsyncClient", return_value=Client()):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run((main.groq_proxy if provider == "groq" else main.nvidia_proxy)(self.request(provider)))
        return caught.exception, len(calls)

    def test_model_not_found(self):
        error, count = self.invoke(httpx.Response(404, json={"error": {
            "code": "model_not_found", "type": "invalid_request_error",
            "message": "Model does not exist or you do not have access."}}))
        self.assertEqual(count, 1)
        self.assertEqual(error.status_code, 404)
        self.assertEqual(error.detail["code"], "model_not_found")
        self.assertEqual(error.detail["source"], "upstream")
        self.assertIn("Render", error.detail["hint"])

    def test_credentials_and_prompt_redacted(self):
        error, _ = self.invoke(httpx.Response(401, json={"error": {
            "code": "gsk_TESTSECRET123", "message":
            "gsk_TESTSECRET123 Bearer otherSecret api_key=anotherSecret private prompt content"},
            "headers": {"Authorization": "never return this"}}))
        serialized = json.dumps(error.detail)
        for secret in ["gsk_TESTSECRET123", "otherSecret", "anotherSecret", "private prompt content", "never return this"]:
            self.assertNotIn(secret, serialized)
        self.assertIn("REDACTED", serialized)

    def test_rate_limit_no_immediate_retry(self):
        error, count = self.invoke(httpx.Response(429, json={"error": {"message": "Rate limit"}}, headers={"Retry-After": "60"}))
        self.assertEqual(count, 1)
        self.assertEqual(error.detail["retry_after_seconds"], 60)

    def test_html_not_exposed(self):
        error, _ = self.invoke(httpx.Response(404, text="<html>private body</html>"))
        self.assertNotIn("private body", str(error.detail))
        self.assertEqual(error.detail["status"], 404)

    def test_nvidia_uses_same_safe_format(self):
        error, _ = self.invoke(httpx.Response(403, json={"error": {"message": "Access denied"}}), "nvidia")
        self.assertEqual(error.detail["provider"], "NVIDIA")
        self.assertEqual(error.status_code, 403)

    def test_success_unchanged(self):
        expected = {"choices": [{"message": {"content": "explanation"}}]}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, *args, **kwargs): return httpx.Response(200, json=expected)
        with patch.object(main.httpx, "AsyncClient", return_value=Client()):
            self.assertEqual(asyncio.run(main.groq_proxy(self.request())), expected)

    def test_connection_failure_does_not_echo_exception(self):
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, *args, **kwargs): raise httpx.ConnectError("gsk_TESTSECRET123")
        with patch.object(main.httpx, "AsyncClient", return_value=Client()):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run(main.groq_proxy(self.request()))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn("gsk_TESTSECRET123", str(caught.exception.detail))


if __name__ == "__main__":
    unittest.main()
