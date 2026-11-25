import os

from locust import HttpUser, between, task


class DeanFlowReader(HttpUser):
    wait_time = between(0.3, 0.7)

    def on_start(self):
        token = os.environ.get("DEANFLOW_LOAD_TOKEN")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    @task(5)
    def list_documents(self):
        self.client.get("/documents", name="/documents", timeout=5)

    @task(3)
    def find_documents(self):
        self.client.get(
            "/documents",
            params={"status_code": "REGISTERED"},
            name="/documents?status_code=REGISTERED",
            timeout=5,
        )

    @task(2)
    def load_lookups(self):
        self.client.get("/documents/lookups", name="/documents/lookups", timeout=5)
