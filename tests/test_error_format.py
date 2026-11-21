"""Catatan analis no. 1, 2, 3: format error seragam, satu pintu login, dan token."""
import json

from app.main import app
from tests.helpers import err_code, make_user


def _all_error_shapes(body: dict) -> bool:
    d = body["detail"]
    return isinstance(d, dict) and "code" in d and "message" in d


def test_error_422_has_single_documented_format(client):
    r = client.post("/api/register", json={"full_name": "A", "email": "x@y.id", "password": "rahasia123", "confirm_password": "beda"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "VALIDATION_ERROR" and d["errors"][0]["field"] == "confirm_password"
    assert "input" not in r.text      # kata sandi yang dikirim tidak boleh dipantulkan kembali


def test_openapi_422_matches_real_response(client):
    """Dokumentasi Swagger harus menulis bentuk 422 yang sama dengan yang dikirim server."""
    spec = app.openapi()
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    for path, item in spec["paths"].items():
        for method, op in item.items():
            r422 = op.get("responses", {}).get("422")
            if r422:
                assert r422["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse"), (method, path)
    err = spec["components"]["schemas"]["ErrorResponse"]
    assert "detail" in err["properties"]


def test_unknown_route_and_wrong_method_use_same_format(client):
    r = client.get("/api/tidak-ada")
    assert r.status_code == 404 and _all_error_shapes(r.json()) and err_code(r) == "NOT_FOUND"
    r = client.delete("/api/login")
    assert r.status_code == 405 and _all_error_shapes(r.json()) and err_code(r) == "METHOD_NOT_ALLOWED"


def test_all_error_kinds_share_one_format(client):
    headers, _ = make_user(client)
    bodies = [
        client.get("/api/gwas/datasets").json(),                                        # 401 tanpa token
        client.get("/api/gwas/analyses/999", headers=headers).json(),                   # 404
        client.post("/api/gwas/analyses", headers=headers, json={}).json(),             # 422
        client.post("/api/login", json={"identifier": "x", "password": "y"}).json(),    # 401 login
    ]
    assert all(_all_error_shapes(b) for b in bodies)


def test_login_single_documented_endpoint(client):
    spec = app.openapi()
    login_paths = [p for p in spec["paths"] if "login" in p]
    assert login_paths == ["/api/login"]
    content = spec["paths"]["/api/login"]["post"]["requestBody"]["content"]
    assert set(content) == {"application/json", "application/x-www-form-urlencoded"}


def test_login_accepts_json_and_form_on_same_endpoint(client):
    client.post("/api/register", json={"full_name": "Uji", "email": "uji@ipb.ac.id", "password": "rahasia123", "confirm_password": "rahasia123"})
    j = client.post("/api/login", json={"identifier": "uji@ipb.ac.id", "password": "rahasia123"})
    f = client.post("/api/login", data={"username": "uji@ipb.ac.id", "password": "rahasia123"})
    assert j.status_code == 200 and f.status_code == 200
    assert j.json()["token_type"] == "bearer" and f.json()["access_token"]


def test_login_json_sent_to_form_style_never_breaks(client):
    """Mengirim JSON ke /login tidak boleh menghasilkan 422 aneh. Alamat lama /login/json juga tetap hidup."""
    client.post("/api/register", json={"full_name": "Uji", "email": "uji@ipb.ac.id", "password": "rahasia123", "confirm_password": "rahasia123"})
    assert client.post("/api/login/json", json={"identifier": "uji@ipb.ac.id", "password": "rahasia123"}).status_code == 200
    assert client.post("/api/login", json={"email": "uji@ipb.ac.id", "password": "rahasia123"}).status_code == 200   # nama kolom alternatif


def test_login_wrong_content_type_and_missing_fields(client):
    r = client.post("/api/login", content="identifier=a&password=b", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415 and err_code(r) == "UNSUPPORTED_MEDIA_TYPE"
    r = client.post("/api/login", json={"identifier": "a"})
    assert r.status_code == 422 and r.json()["detail"]["errors"] == [{"field": "password", "message": "Kata sandi wajib diisi."}]
    r = client.post("/api/login", content="{rusak", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and err_code(r) == "VALIDATION_ERROR"


def test_login_response_gives_token_fields_frontend_must_store(client):
    client.post("/api/register", json={"full_name": "Uji", "email": "uji@ipb.ac.id", "password": "rahasia123", "confirm_password": "rahasia123"})
    body = client.post("/api/login", json={"identifier": "uji@ipb.ac.id", "password": "rahasia123"}).json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"} and body["expires_in"] > 0


def test_protected_endpoints_need_the_saved_token(client):
    headers, _ = make_user(client)
    assert client.get("/api/gwas/datasets").status_code == 401
    r = client.get("/api/gwas/datasets")
    assert err_code(r) == "TOKEN_MISSING" and r.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/gwas/datasets", headers={"Authorization": "Bearer salah"}).status_code == 401
    assert client.get("/api/gwas/datasets", headers=headers).status_code == 200
    assert client.get("/api/me", headers=headers).status_code == 200


def test_cors_allows_authorization_header(client):
    r = client.options("/api/gwas/datasets", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
                                                      "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200 and "authorization" in r.headers["access-control-allow-headers"].lower()
