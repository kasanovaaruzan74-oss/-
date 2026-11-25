"""8-зертханалық жұмыс үшін жергілікті уақытша сервердің нақты HTTP жауаптарын жазу."""

import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import requests


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-log", type=Path, help="Uvicorn журналында құпия деректердің жарияланбағанын тексеру")
    args = parser.parse_args()
    base = urlsplit(args.base_url)
    if (
        base.scheme != "http"
        or base.hostname not in {"127.0.0.1", "localhost", "::1"}
        or base.username is not None
        or base.password is not None
        or base.path not in {"", "/"}
        or base.query
        or base.fragment
    ):
        parser.error("--base-url жергілікті loopback мекенжайындағы уақытша HTTP серверін көрсетуі тиіс")
    return args


class ApiExamples:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        self.results = []

    def call(self, method, path, **kwargs):
        return self.session.request(
            method, self.base_url + path, timeout=10, allow_redirects=False, **kwargs
        )

    def check(self, name, method, path, expected_status, **kwargs):
        response = self.call(method, path, **kwargs)
        body = response.json() if response.content else None
        assert response.status_code == expected_status, f"{name}: HTTP {response.status_code}; {body}"
        self.results.append(
            {
                "scenario": name,
                "method": method,
                "path": path,
                "status": response.status_code,
                "response": body,
                "passed": True,
            }
        )
        return body

    def login(self, email, password):
        response = self.call("POST", "/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, "Оқу тіркелгісіне кіру мүмкін болмады"
        return {"Authorization": "Bearer " + response.json()["access_token"]}


def main():
    args = arguments()
    examples = ApiExamples(args.base_url)
    password = f"Lab8!Z2-{secrets.token_urlsafe(16)}"
    registration = {
        "email": f"lab08-{secrets.token_hex(5)}@example.invalid",
        "full_name": "Оқу орындаушысы",
        "password": password,
    }
    admin_password = os.environ["DEANFLOW_ADMIN_PASSWORD"]
    admin = examples.login(os.environ.get("DEANFLOW_ADMIN_EMAIL", "dana@example.invalid"), admin_password)
    for name, bad_password in [("Бос құпиясөз", ""), ("Әлсіз құпиясөз", "12345")]:
        examples.check(name, "POST", "/auth/register", 400, json={**registration, "password": bad_password})
    examples.check(
        "Белгісіз логин",
        "POST",
        "/auth/login",
        401,
        json={"email": "missing@example.invalid", "password": password},
    )
    examples.check("Орындаушыны тіркеу", "POST", "/auth/register", 201, json=registration)
    user = examples.login(registration["email"], password)
    examples.check("Орындаушының профилін оқу", "GET", "/auth/me", 200, headers=user)

    examples.check("Токенсіз қол жеткізу", "GET", "/documents", 401)
    examples.check("Пайдаланушының белгісіз идентификаторы", "GET", "/auth/users/999999", 404, headers=admin)
    examples.check("Құжаттар тізімін оқу", "GET", "/documents?limit=1", 200, headers=user)
    lookup = examples.call("GET", "/documents/lookups", headers=user)
    assert lookup.status_code == 200
    references = lookup.json()
    payload = {
        "title": "№ 8 зертханалық жұмысқа арналған оқу өтініші",
        "document_type_id": references["types"][0]["id"],
        "status_id": references["statuses"][0]["id"],
    }
    created = examples.check(
        "Құжат жасау",
        "POST",
        "/documents",
        201,
        headers=user,
        json=payload,
    )
    path = f"/documents/{created['document_id']}"
    document = examples.check("Идентификатор бойынша оқу", "GET", path, 200, headers=user)
    assert document == created
    replaced = examples.check(
        "Құжат өрістерін ауыстыру",
        "PUT",
        path,
        200,
        headers=user,
        json={**payload, "title": "Жаңартылған оқу өтініші"},
    )
    assert replaced["title"] == "Жаңартылған оқу өтініші"
    assert replaced["registration_number"] == created["registration_number"]
    examples.check("Орындаушыға жоюға тыйым салынған", "DELETE", path, 403, headers=user)
    examples.check("Әкімшінің құжатты жоюы", "DELETE", path, 204, headers=admin)
    examples.check("Жоқ құжатты оқу", "GET", path, 404, headers=user)
    examples.check("Бос деректер", "POST", "/documents", 400, headers=user, json={})
    examples.check(
        "Жарамсыз JSON",
        "POST",
        "/documents",
        400,
        headers={**user, "Content-Type": "application/json"},
        data='{"title":',
    )

    evidence = json.dumps(examples.results, ensure_ascii=False, indent=2)
    credentials = [
        password,
        admin_password,
        admin["Authorization"].split(" ", 1)[1],
        user["Authorization"].split(" ", 1)[1],
    ]
    assert all(secret not in evidence for secret in credentials)
    if args.server_log is not None:
        log = args.server_log.read_text(encoding="utf-8")
        assert "POST /auth/login HTTP/" in log
        assert "POST /auth/register HTTP/" in log
        assert all(secret not in log for secret in credentials)
        print("Uvicorn журналы сұрауларды тіркеді. Журнал мен жауаптарда құпиясөздер мен токендер жоқ.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(evidence + "\n", encoding="utf-8")
    print(f"Тексерілген HTTP сценарийлерінің саны: {len(examples.results)}")


if __name__ == "__main__":
    main()
