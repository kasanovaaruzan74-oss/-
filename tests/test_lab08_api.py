import logging
import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database.db import create_database_engine, get_session
from app.database.models import Department, DocumentStatus, DocumentType, LoginSession, UserCredential
from app.main import create_app
from app.security import configure_demo_admin


ADMIN_EMAIL = "admin-test@example.invalid"
ACCOUNT_EMAIL = "executor-test@example.invalid"
ACCOUNT_NAME = "Оқу орындаушысы"


def strong_password() -> str:
    return f"Lab8!Z2-{secrets.token_urlsafe(16)}"


@pytest.fixture
def admin_password():
    return strong_password()


@pytest.fixture
def protected_client(tmp_path, admin_password) -> Iterator[TestClient]:
    database_engine = create_database_engine(f"sqlite:///{tmp_path / 'protected.db'}")
    application = create_app(database_engine, auth_required=True)
    application.state.database_engine = database_engine

    def get_test_session():
        with Session(database_engine) as session:
            yield session

    application.dependency_overrides[get_session] = get_test_session
    try:
        with TestClient(application) as client:
            with Session(database_engine) as session:
                session.add_all(
                    [
                        Department(department_name="Оқу бөлімі"),
                        DocumentStatus(status_name="Тіркелді", status_code="REGISTERED"),
                        DocumentType(type_name="Өтініш", code="REQUEST"),
                    ]
                )
                configure_demo_admin(session, ADMIN_EMAIL, admin_password)
            yield client
    finally:
        database_engine.dispose()


def register(client: TestClient, password: str):
    return client.post(
        "/auth/register",
        json={"email": ACCOUNT_EMAIL, "password": password, "full_name": ACCOUNT_NAME},
    )


def auth_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"].lower() == "bearer"
    assert body["access_token"]
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def executor_headers(protected_client):
    password = strong_password()
    assert register(protected_client, password).status_code == 201
    return auth_headers(protected_client, ACCOUNT_EMAIL, password)


@pytest.fixture
def admin_headers(protected_client, admin_password):
    return auth_headers(protected_client, ADMIN_EMAIL, admin_password)


@pytest.fixture
def document_data():
    return {
        "title": "Анықтама беру туралы өтініш",
        "description": "Тіркеуді тексеру",
        "document_type_id": 1,
        "status_id": 1,
        "department_id": 1,
        "direction": "Кіріс",
        "priority": "Қалыпты",
        "due_date": "2099-01-12",
    }


def create_document(client, headers, data):
    response = client.post("/documents", json=data, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_list_documents_returns_json(protected_client, executor_headers):
    response = protected_client.get("/documents", headers=executor_headers)

    assert response.status_code == 200
    assert response.json() == []


def test_create_document_assigns_authenticated_creator(protected_client, executor_headers, document_data):
    current_user = protected_client.get("/auth/me", headers=executor_headers).json()

    created = create_document(protected_client, executor_headers, document_data)
    response = protected_client.get(f"/documents/{created['document_id']}", headers=executor_headers)

    assert created["title"] == document_data["title"]
    assert created["created_by_user_id"] == current_user["user_id"]
    assert created["registration_number"].startswith("DF-")
    assert response.status_code == 200
    assert response.json() == created


def test_put_replaces_mutable_fields(protected_client, executor_headers, document_data):
    created = create_document(protected_client, executor_headers, document_data)
    path = f"/documents/{created['document_id']}"

    result = protected_client.put(
        path,
        headers=executor_headers,
        json={"title": "Жаңартылған өтініш", "document_type_id": 1},
    )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["document_id"] == created["document_id"]
    assert body["title"] == "Жаңартылған өтініш"
    assert body["description"] is None
    assert body["due_date"] is None
    assert body["department_id"] is None
    assert body["registration_number"] == created["registration_number"]
    assert body["created_by_user_id"] == created["created_by_user_id"]
    assert protected_client.get(path, headers=executor_headers).json() == body


def test_delete_requires_administrator_and_removes_document(
    protected_client, admin_headers, document_data,
):
    created = create_document(protected_client, admin_headers, document_data)
    path = f"/documents/{created['document_id']}"

    deleted = protected_client.delete(path, headers=admin_headers)

    assert deleted.status_code == 204
    assert deleted.content == b""
    missing = protected_client.get(path, headers=admin_headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]


def test_missing_document_id_returns_404(protected_client, executor_headers):
    response = protected_client.get("/documents/999999", headers=executor_headers)

    assert response.status_code == 404
    assert response.json()["detail"]


def test_empty_document_payload_is_rejected(protected_client, executor_headers):
    response = protected_client.post("/documents", headers=executor_headers, json={})

    assert response.status_code == 400
    assert response.json()["detail"]
    assert protected_client.get("/documents", headers=executor_headers).json() == []


def test_malformed_json_is_rejected_without_reflecting_body(protected_client, executor_headers):
    marker = f"body-{secrets.token_hex(8)}"
    response = protected_client.post(
        "/documents",
        headers={**executor_headers, "Content-Type": "application/json"},
        content=f'{{"title":"{marker}"',
    )

    assert response.status_code == 400
    assert response.json()["detail"]
    assert marker not in response.text


@pytest.mark.parametrize("title", ["Акт", "А" * 250], ids=["min-length", "max-length"])
def test_document_title_accepts_boundary_lengths(protected_client, executor_headers, document_data, title):
    created = create_document(protected_client, executor_headers, {**document_data, "title": title})

    assert created["title"] == title


@pytest.mark.parametrize("title", ["  ", "А" * 251], ids=["blank", "too-long"])
def test_document_title_rejects_invalid_lengths(protected_client, executor_headers, document_data, title):
    response = protected_client.post(
        "/documents", headers=executor_headers, json={**document_data, "title": title}
    )

    assert response.status_code == 400
    assert protected_client.get("/documents", headers=executor_headers).json() == []


@pytest.mark.parametrize("password", ["", "12345"], ids=["empty", "weak"])
def test_register_rejects_empty_or_weak_password(protected_client, password):
    response = register(protected_client, password)

    assert response.status_code == 400
    assert response.json()["detail"]


def test_wrong_login_rejects_account_and_redacts_credentials(protected_client, caplog):
    caplog.set_level(logging.INFO)
    password = strong_password()
    assert register(protected_client, password).status_code == 201
    wrong_password = strong_password()

    wrong_login = protected_client.post(
        "/auth/login", json={"email": "unknown@example.invalid", "password": password}
    )
    wrong_secret = protected_client.post(
        "/auth/login", json={"email": ACCOUNT_EMAIL, "password": wrong_password}
    )

    assert wrong_login.status_code == wrong_secret.status_code == 401
    assert wrong_login.json() == wrong_secret.json()
    for secret in (password, wrong_password):
        assert secret not in wrong_login.text
        assert secret not in wrong_secret.text
        assert secret not in caplog.text


def test_document_routes_require_authentication(protected_client):
    for path in ("/documents", "/documents/lookups", "/auth/me"):
        response = protected_client.get(path)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_registered_executor_cannot_delete_document(protected_client, executor_headers, document_data):
    profile = protected_client.get("/auth/me", headers=executor_headers)
    created = create_document(protected_client, executor_headers, document_data)
    path = f"/documents/{created['document_id']}"

    response = protected_client.delete(path, headers=executor_headers)

    assert profile.status_code == 200
    assert profile.json()["role"] == "Executor"
    assert response.status_code == 403
    assert response.json()["detail"]
    assert protected_client.get(path, headers=executor_headers).json() == created


def test_successful_authentication_exposes_only_public_identity(protected_client, caplog):
    caplog.set_level(logging.INFO)
    password = strong_password()
    signup = register(protected_client, password)
    headers = auth_headers(protected_client, ACCOUNT_EMAIL, password)
    profile = protected_client.get("/auth/me", headers=headers)
    token = headers["Authorization"].split(" ", 1)[1]

    assert signup.status_code == 201
    assert profile.status_code == 200
    assert set(profile.json()) == {"user_id", "email", "full_name", "role"}
    assert profile.json()["email"] == ACCOUNT_EMAIL
    assert profile.json()["full_name"] == ACCOUNT_NAME
    for secret in (password, token):
        assert secret not in signup.text
        assert secret not in profile.text
        assert secret not in caplog.text


def test_administrator_receives_404_for_unknown_user(protected_client, admin_headers):
    response = protected_client.get("/auth/users/999999", headers=admin_headers)

    assert response.status_code == 404
    assert response.json()["detail"]


def test_logout_revokes_access_to_protected_resources(protected_client, executor_headers):
    response = protected_client.post("/auth/logout", headers=executor_headers)

    assert response.status_code == 204
    assert response.content == b""
    assert protected_client.get("/auth/me", headers=executor_headers).status_code == 401
    assert protected_client.get("/documents", headers=executor_headers).status_code == 401



def test_password_and_token_are_saved_only_as_hashes(protected_client):
    password = strong_password()
    signup = register(protected_client, password)
    user_id = signup.json()["user_id"]
    headers = auth_headers(protected_client, ACCOUNT_EMAIL, password)
    token = headers["Authorization"].split(" ", 1)[1]

    with Session(protected_client.app.state.database_engine) as session:
        credential = session.get(UserCredential, user_id)
        token_row = session.exec(
            select(LoginSession).where(LoginSession.user_id == user_id)
        ).one()
        assert credential.password_hash
        assert password not in credential.password_hash
        assert token_row.token_hash
        assert token_row.token_hash != token
