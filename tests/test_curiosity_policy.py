from agent.curiosity import AutonomousCuriosity, contains_sensitive_content, is_autonomous_path_allowed


ROOTS = ["D:\\", "E:\\", "G:\\"]


def test_approved_game_and_workspace_paths_are_allowed():
    assert is_autonomous_path_allowed("G:\\SteamLibrary\\steamapps\\common\\Game\\readme.txt", ROOTS)
    assert is_autonomous_path_allowed("E:\\workspace\\projects\\mina\\notes.md", ROOTS)


def test_unapproved_drive_is_denied():
    assert not is_autonomous_path_allowed("C:\\Users\\Admin\\notes.txt", ROOTS)


def test_windows_browser_and_secret_paths_are_denied():
    assert not is_autonomous_path_allowed("E:\\Windows\\System32\\config\\SAM", ROOTS)
    assert not is_autonomous_path_allowed("E:\\WindowsApps\\Package\\data.txt", ROOTS)
    assert not is_autonomous_path_allowed("D:\\Users\\Admin\\AppData\\Local\\Microsoft\\Edge\\User Data\\Cookies", ROOTS)
    assert not is_autonomous_path_allowed("G:\\games\\server\\.env", ROOTS)
    assert not is_autonomous_path_allowed("E:\\workspace\\private-token.txt", ROOTS)


def test_sensitive_content_is_not_cataloged():
    assert contains_sensitive_content("api_key = abcdefghijklmnop")
    assert contains_sensitive_content("-----BEGIN PRIVATE KEY-----")
    assert not contains_sensitive_content("Patch notes: improved controller support and performance.")


class FakeMemory:
    def __init__(self):
        self.writes = []

    def recent_memories(self, **_kwargs):
        return []

    def add_memory(self, text, kind, tags):
        self.writes.append({"text": text, "kind": kind, "tags": tags})
        return len(self.writes)


def test_useful_work_runs_without_conversation_topics_and_is_deduplicated():
    memory = FakeMemory()
    executions = []
    curiosity = AutonomousCuriosity(
        memory=memory,
        tools=object(),
        approved_roots=ROOTS,
        work_provider=lambda: [{"key": "todo:one", "title": "Review task"}],
        work_executor=lambda task: executions.append(task) or {"ok": True, "summary": "reviewed"},
    )

    curiosity._run_cycle(do_local=False, do_news=False, do_work=True)
    curiosity._run_cycle(do_local=False, do_news=False, do_work=True)

    assert len(executions) == 1
    assert memory.writes[0]["tags"] == ["autonomous_work", "audit", "success"]
    assert curiosity.status()["last_work_result"]["result"]["summary"] == "reviewed"
    assert curiosity.events_since()[0]["source"] == "work"


def test_failed_useful_work_stops_after_retry_limit():
    executions = []
    curiosity = AutonomousCuriosity(
        memory=FakeMemory(),
        tools=object(),
        approved_roots=ROOTS,
        max_work_retries=2,
        work_provider=lambda: [{"key": "todo:failing", "title": "Failing task"}],
        work_executor=lambda task: executions.append(task) or {"ok": False, "summary": "failed"},
    )

    for _ in range(3):
        curiosity._run_cycle(do_local=False, do_news=False, do_work=True)

    assert len(executions) == 2