"""User-level: a human operator talks to the web UI."""


def test_unauthenticated_human_is_sent_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/login" in response.headers["Location"]


def test_human_with_bad_password_is_rejected_and_logged(client, runtime):
    response = client.post(
        "/login",
        data={"username": "harvey", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert b"Invalid" in response.data or b"invalid" in response.data.lower()
    logs = runtime["plugins"]["accounting"].logs_all()
    assert any(row["verb"] == "authn" and row["decision"] == "deny" for row in logs)


def test_human_sees_authorized_lakes_and_host_storage(client):
    client.post("/login", data={"username": "harvey", "password": "human-test-pass"})
    page = client.get("/")
    assert page.status_code == 200
    body = page.data
    assert b"Financial files" in body
    assert b"OLAP lab" in body
    assert b"Host free" in body or b"host free" in body.lower()


def test_human_opens_lake_and_sees_inventory_and_log(client):
    client.post("/login", data={"username": "harvey", "password": "human-test-pass"})
    page = client.get("/lakes/financial_files")
    assert page.status_code == 200
    assert b"funding_rates.parquet" in page.data
    assert b"Usage log" in page.data
