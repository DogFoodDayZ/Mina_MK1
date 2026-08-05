from tools.file_access_policy import authorize_write_paths


def test_authorize_write_paths_allows_repo_root_without_external_auth():
    ok, error, normalized = authorize_write_paths(
        [r"E:\Mina_MK1\tmp\policy_probe.txt"],
        authorized_write=False,
    )

    assert ok is True
    assert error is None
    assert normalized and normalized[0].lower().endswith(r"mina_mk1\tmp\policy_probe.txt")


def test_authorize_write_paths_blocks_outside_all_configured_roots(monkeypatch):
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOT", r"E:\workspace")
    monkeypatch.setenv("MK1_FULL_ACCESS_ROOTS", r"C:\dev")

    ok, error, _ = authorize_write_paths(
        [r"D:\elsewhere\policy_probe.txt"],
        authorized_write=False,
    )

    assert ok is False
    assert error == r"write_not_authorized_outside_workspace: set authorized_write=true for writes outside E:\workspace"