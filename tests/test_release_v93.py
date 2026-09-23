import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
import main
from ai_contract import VERSION, SCHEMA

class ReleaseTests(unittest.TestCase):
    def request(self):
        return {"api_key":"gsk_TEST_ONLY", "review_version":VERSION,
                "body":{"model":"openai/gpt-oss-120b", "messages":[{"role":"user","content":"test"}]}}

    def test_server_owned_schema(self):
        request=self.request();request['body']['response_format']={'malicious':'override'}
        _,body=main._request_parts(request,'groq')
        self.assertEqual(body['response_format']['json_schema']['schema'],SCHEMA)
        self.assertTrue(body['response_format']['json_schema']['strict'])
        self.assertEqual(body['max_completion_tokens'],4096)

    def test_incompatible_version_rejected(self):
        request=self.request();request['review_version']='future'
        with self.assertRaises(main.HTTPException):main._request_parts(request,'groq')

    def test_static_and_health(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/health').json()['ai_analysis'],VERSION)
            for url in ['/','/app.js','/ai-review.js','/ai-schema.json','/cache/predictions.json','/cache/ai-context/2606.json']:
                self.assertEqual(client.get(url).status_code,200,url)
            self.assertIn('ai-review.js?v=94',client.get('/').text)

    def test_one_attempt_on_all_errors(self):
        for status in [400,401,404,408,429,500,502,503,504]:
            upstream=AsyncMock();upstream.post.return_value=httpx.Response(status,json={'error':{'message':'gsk_TEST_ONLY'}})
            context=AsyncMock();context.__aenter__.return_value=upstream
            with TestClient(main.app) as client,patch('main.httpx.AsyncClient',return_value=context):
                r=client.post('/api/groq',json=self.request())
            self.assertEqual(r.status_code,status)
            self.assertEqual(upstream.post.await_count,1)
            self.assertNotIn('gsk_TEST_ONLY',r.text)

    def test_invalid_upstream_json(self):
        upstream=AsyncMock();upstream.post.return_value=httpx.Response(200,text='not json')
        context=AsyncMock();context.__aenter__.return_value=upstream
        with TestClient(main.app) as client,patch('main.httpx.AsyncClient',return_value=context):
            self.assertEqual(client.post('/api/groq',json=self.request()).status_code,502)

    def test_success_end_to_end_proxy(self):
        expected={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
        upstream=AsyncMock();upstream.post.return_value=httpx.Response(200,json=expected)
        context=AsyncMock();context.__aenter__.return_value=upstream
        with TestClient(main.app) as client,patch('main.httpx.AsyncClient',return_value=context):
            self.assertEqual(client.post('/api/groq',json=self.request()).json(),expected)
        sent=upstream.post.call_args.kwargs['json']
        self.assertEqual(sent['response_format']['json_schema']['schema'],SCHEMA)
        self.assertNotIn('api_key',sent)

if __name__=='__main__':unittest.main()
