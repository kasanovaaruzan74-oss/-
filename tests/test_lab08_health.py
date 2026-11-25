from fastapi.testclient import TestClient


def test_health_response_contract(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"күйі": "жұмыс істеп тұр"}
