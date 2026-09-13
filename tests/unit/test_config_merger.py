from app.config_merger import convert_type, merge_config, process_unknown_args


def test_process_unknown_and_types(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--load_config", "x.json", "--web_port", "9"],
    )
    unknown = process_unknown_args(["--extra", "1", "--flag"])
    assert unknown["extra"] == "1"
    assert unknown["flag"] is True
    assert convert_type("3") == 3
    assert convert_type("true") is True
    merged = merge_config(
        {"web_port": 1, "a": 0},
        [{"a": 2}],
        {"a": 3},
        {"web_port": 9, "load_config": "x.json"},
        unknown,
    )
    assert merged["a"] == 3
    assert merged["web_port"] == 9
    assert merged["load_config"] == "x.json"
