import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend import main


class IntegrationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = main.DB_PATH
        main.DB_PATH = Path(self.temp_dir.name) / "integration-test.sqlite3"
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.transport_calls = 0

    def tearDown(self):
        self.client.__exit__(None, None, None)
        main.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def mock_upstream(self, responder):
        real_client = httpx.AsyncClient

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(responder), **kwargs)

        patcher = patch("backend.main.httpx.AsyncClient", side_effect=client_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dashboard_and_public_integrations(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok", "database": "connected"})
        providers = self.client.get("/api/integrations").json()
        self.assertEqual({item["id"] for item in providers}, set(main.PROVIDERS))
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(len(dashboard["providers"]), 3)
        self.assertEqual(dashboard["requests_today"], 0)
        self.assertIn("API Connect", self.client.get("/").text)

    def test_allowlisted_call_is_recorded_and_detail_keeps_response(self):
        payload = [{"id": 1, "title": "Hello"}]

        def responder(request):
            self.transport_calls += 1
            self.assertEqual(request.url.host, "jsonplaceholder.typicode.com")
            self.assertEqual(request.url.path, "/posts")
            self.assertEqual(request.url.params["_limit"], "5")
            return httpx.Response(200, json=payload)

        self.mock_upstream(responder)
        response = self.client.post(
            "/api/integrations/jsonplaceholder/posts.list",
            json={"parameters": {"limit": 5}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-ratelimit-remaining"], "59")
        body = response.json()
        self.assertEqual(body["data"], payload)
        history = self.client.get("/api/logs").json()
        self.assertEqual(history["total"], 1)
        record = self.client.get(f"/api/logs/{body['request_id']}").json()
        self.assertEqual(record["response"], payload)
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["successful_24h"], 1)
        self.assertEqual(self.transport_calls, 1)

    def test_parameter_validation_prevents_unapproved_requests(self):
        response = self.client.post(
            "/api/integrations/github/repositories.get",
            json={"parameters": {"owner": "owner", "repo": "../../other"}},
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/integrations/jsonplaceholder/posts.get",
            json={"parameters": {"id": 1, "url": "http://127.0.0.1/admin"}},
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/integrations/open-meteo/weather.current",
            json={"parameters": {"latitude": 120, "longitude": 0}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.transport_calls, 0)
        logs = self.client.get("/api/logs?outcome=invalid_request").json()
        self.assertEqual(logs["total"], 3)

    def test_configurable_rate_limit_blocks_before_upstream_call(self):
        self.assertEqual(
            self.client.patch(
                "/api/integrations/jsonplaceholder/settings",
                json={"rate_limit_per_minute": 1},
            ).json()["rate_limit_per_minute"],
            1,
        )
        self.mock_upstream(lambda request: httpx.Response(200, json={"ok": True}))
        first = self.client.post("/api/integrations/jsonplaceholder/posts.list", json={"parameters": {}})
        second = self.client.post("/api/integrations/jsonplaceholder/posts.list", json={"parameters": {}})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["x-ratelimit-remaining"], "0")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["x-ratelimit-limit"], "1")
        self.assertEqual(self.client.get("/api/logs?outcome=rate_limited").json()["total"], 1)

    def test_disabled_provider_is_logged_without_calling_upstream(self):
        self.client.patch(
            "/api/integrations/jsonplaceholder/settings",
            json={"enabled": False},
        )
        response = self.client.post("/api/integrations/jsonplaceholder/posts.list", json={"parameters": {}})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get("/api/logs?outcome=disabled").json()["total"], 1)

    def test_upstream_errors_are_returned_and_stored(self):
        self.mock_upstream(lambda request: httpx.Response(429, json={"message": "limited"}))
        response = self.client.post("/api/integrations/github/repositories.get", json={
            "parameters": {"owner": "fastapi", "repo": "fastapi"},
        })
        self.assertEqual(response.status_code, 502)
        self.assertIn("HTTP 429", response.json()["detail"])
        history = self.client.get("/api/logs?outcome=upstream_error").json()
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["items"][0]["status_code"], 502)

    def test_upstream_redirects_are_not_followed(self):
        requests = []

        def responder(request):
            requests.append(request.url)
            return httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1/private"},
                json={"message": "redirect"},
            )

        self.mock_upstream(responder)
        response = self.client.post("/api/integrations/jsonplaceholder/posts.list", json={"parameters": {}})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].host, "jsonplaceholder.typicode.com")
        self.assertEqual(self.client.get("/api/logs?outcome=upstream_error").json()["total"], 1)

    def test_log_filters_and_clear_history(self):
        main.record_log(
            "github", "repositories.get", "GET", "https://api.github.com/repos/a/b",
            200, "success", 12, {"owner": "a", "repo": "b"}, {"name": "b"},
        )
        self.assertEqual(self.client.get("/api/logs?q=github").json()["total"], 1)
        self.assertEqual(self.client.get("/api/logs?provider=github&limit=1").json()["total"], 1)
        self.assertEqual(self.client.delete("/api/logs").status_code, 204)
        self.assertEqual(self.client.get("/api/logs").json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
