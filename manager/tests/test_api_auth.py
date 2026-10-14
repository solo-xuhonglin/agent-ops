def test_rejects_missing_or_wrong_key(client):
    missing = client.get("/v1/agent")
    wrong = client.get("/v1/agent", headers={"Authorization": "Bearer wrong-key"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth"
    assert wrong.status_code == 401


def test_get_agent_returns_input_declaration(client, auth_headers):
    response = client.get("/v1/agent", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert [param["key"] for param in body["input"]] == ["project_id"]
    assert body["input"][0]["required"] is True
    assert body["input"][0]["description"]


def test_new_session(client, auth_headers):
    response = client.post("/v1/sessions", headers=auth_headers)

    assert response.status_code == 201
    assert response.json()["session_id"]
