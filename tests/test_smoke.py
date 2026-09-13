from app.plugin_loader import load_plugin


def test_entry_points():
    for group, name in [
        ("datagov.pipeline", "default_pipeline"),
        ("datagov.web", "default_web"),
        ("datagov.access", "default_access"),
        ("datagov.accounting", "default_accounting"),
        ("datagov.lake", "files_lake"),
        ("datagov.lake", "sql_lake"),
        ("datagov.role", "default_role"),
    ]:
        cls, _ = load_plugin(group, name)
        assert cls.plugin_params is not None
