from agent.curiosity import (
    AutonomousCuriosity,
    contains_sensitive_content,
    is_autonomous_discovery_path_relevant,
    is_autonomous_news_result_relevant,
    is_autonomous_path_allowed,
)


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


def test_discovery_path_requires_topic_match_and_rejects_backup_or_generated_artifacts():
    assert is_autonomous_discovery_path_relevant("E:\\Mina_MK1\\agent\\core.py", ["core"])
    assert not is_autonomous_discovery_path_relevant("G:\\.openclaw_backup\\workspace\\check-haven-shortcut.ps1", ["haven"])
    assert not is_autonomous_discovery_path_relevant("E:\\Mina_MK1\\avatar-overlay\\package-lock.json", ["avatar"])


def test_news_result_requires_trusted_source_and_active_topic():
    python_docs = {
        "title": "Python docs - sqlite3 module",
        "url": "https://docs.python.org/3/library/sqlite3.html",
        "snippet": "Python sqlite3 documentation.",
    }
    unrelated_wikipedia = {
        "title": "Once (film) - Wikipedia",
        "url": "https://en.wikipedia.org/wiki/Once_(film)",
        "snippet": "A 2007 musical drama film.",
    }

    assert is_autonomous_news_result_relevant(python_docs, ["python"])
    assert not is_autonomous_news_result_relevant(python_docs, ["ostranauts"])
    assert not is_autonomous_news_result_relevant(unrelated_wikipedia, ["music"])


class FakeMemory:
    def __init__(self):
        self.writes = []

    def recent_memories(self, **_kwargs):
        return []

    def add_memory(self, text, kind, tags):
        self.writes.append({"text": text, "kind": kind, "tags": tags})
        return len(self.writes)


class FakeTools:
    def __init__(self, rows):
        self.tools = {"web_search": object()}
        self._rows = rows
        self.queries = []

    def run(self, name, args):
        self.queries.append((name, dict(args or {})))
        return {
            "ok": True,
            "result": {"results": list(self._rows)},
            "error": None,
        }


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


def test_research_only_work_is_not_labeled_as_success_or_failure():
    memory = FakeMemory()
    curiosity = AutonomousCuriosity(
        memory=memory,
        tools=object(),
        approved_roots=ROOTS,
        work_provider=lambda: [{"key": "todo:research", "title": "Research task"}],
        work_executor=lambda _task: {
            "ok": False,
            "outcome": "research_complete",
            "summary": "Inspected the relevant files.",
        },
    )

    curiosity._run_cycle(do_local=False, do_news=False, do_work=True)

    assert memory.writes[0]["tags"] == ["autonomous_work", "audit", "research"]
    assert curiosity.events_since()[0]["outcome"] == "research_complete"


def test_deferred_useful_work_does_not_consume_a_retry_attempt():
    executions = []
    curiosity = AutonomousCuriosity(
        memory=FakeMemory(),
        tools=object(),
        approved_roots=ROOTS,
        max_work_retries=2,
        work_provider=lambda: [{"key": "todo:deferred", "title": "Deferred task"}],
        work_executor=lambda task: executions.append(task) or {"ok": False, "deferred": True, "summary": "user active"},
    )

    for _ in range(5):
        curiosity._run_cycle(do_local=False, do_news=False, do_work=True)

    # A task that only ever defers should keep being retried, not get burned through
    # its retry budget and disappear.
    assert len(executions) == 5


def test_news_scan_accepts_ostranauts_wiki_results_and_stores_memory():
    memory = FakeMemory()
    tools = FakeTools([
        {
            "title": "Ostranauts Wiki - Medical Bay",
            "url": "https://ostranauts.wiki.gg/wiki/Medical_Bay",
            "snippet": "Ship module details and gameplay notes.",
        }
    ])
    curiosity = AutonomousCuriosity(memory=memory, tools=tools, approved_roots=ROOTS)

    curiosity._scan_news(["ostranauts"])

    assert len(memory.writes) == 1
    assert memory.writes[0]["tags"] == ["autonomous_curiosity", "relevant_discovery", "news"]
    assert "ostranauts.wiki.gg" in memory.writes[0]["text"].lower()
    assert tools.queries


def test_news_scan_accepts_coding_reference_results_and_stores_memory():
    memory = FakeMemory()
    tools = FakeTools([
        {
            "title": "Python docs - sqlite3 module",
            "url": "https://docs.python.org/3/library/sqlite3.html",
            "snippet": "sqlite3 is a DB-API 2.0 interface for SQLite databases.",
        }
    ])
    curiosity = AutonomousCuriosity(memory=memory, tools=tools, approved_roots=ROOTS)

    curiosity._scan_news(["python"])

    assert len(memory.writes) == 1
    assert memory.writes[0]["tags"] == ["autonomous_curiosity", "relevant_discovery", "news"]
    assert "docs.python.org" in memory.writes[0]["text"].lower()
    assert tools.queries