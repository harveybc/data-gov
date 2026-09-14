from tests.conftest import HUMAN_PASS, PREDICTOR_KEY


def test_dashboard_detail_and_api_distinguish_stores(client):
    response = client.post("/login", data={"username": "harvey", "password": HUMAN_PASS}, follow_redirects=True)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Data stores" in page and "Stores you can see" in page
    assert ">warehouse<" in page and ">lake<" in page
    assert "Data lakes" not in page
    warehouse = client.get("/lakes/olap_lab").get_data(as_text=True)
    assert ">warehouse<" in warehouse
    assert "Bytes in this store" in warehouse
    assert "Bytes in this lake" not in warehouse
    rows = client.get("/api/v1/lakes", headers={"Authorization": f"Bearer {PREDICTOR_KEY}"}).get_json()["lakes"]
    kinds = {row["lake_id"]: row["kind"] for row in rows}
    assert kinds["olap_lab"] == "warehouse"
    assert kinds["lab_files"] == "lake"
