from corecoder import Agent
from corecoder import session as session_module
from corecoder.llm import LLM
from corecoder.session import load_session, save_session


def test_default_session_ids_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = [{"role": "user", "content": "first"}]
    first_id = save_session(agent, "model-a")

    agent.messages = [{"role": "user", "content": "second"}]
    second_id = save_session(agent, "model-b")

    assert first_id != second_id
    data = load_session(agent, first_id)
    assert data is not None
    assert data["model"] == "model-a"
    assert agent.messages == [{"role": "user", "content": "first"}]

    data = load_session(agent, second_id)
    assert data is not None
    assert data["model"] == "model-b"
    assert agent.messages == [{"role": "user", "content": "second"}]


def test_session_id_path_traversal_is_neutralized(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = [{"role": "user", "content": "x"}]
    sid = save_session(agent, "m", "../../etc/passwd")

    assert sid == "passwd"
    assert (tmp_path / "passwd.json").exists()
    # the same traversal string round-trips through the parent-dir boundary check
    data = load_session(agent, "../../etc/passwd")
    assert data is not None
    assert agent.messages == [{"role": "user", "content": "x"}]
    assert data["model"] == "m"


def test_session_id_absolute_path_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = [{"role": "user", "content": "x"}]
    sid = save_session(agent, "m", "/etc/shadow")

    assert sid == "shadow"
    assert (tmp_path / "shadow.json").exists()


def test_session_id_windows_backslash_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = [{"role": "user", "content": "x"}]
    sid = save_session(agent, "m", r"..\..\secret")

    assert sid == "secret"


def test_session_id_length_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    agent.message = [{"role": "user", "content": "x"}]
    sid = save_session(agent, "m", "a" * 500)

    assert len(sid) <= 100
    assert (tmp_path / f"{sid}.json").exists()


def test_corrupt_session_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    agent = Agent(llm=LLM.__new__(LLM))
    (tmp_path / "broken.json").write_text("{ not valid json", encoding="utf-8")

    assert load_session(agent, "broken") is None


def test_session_roundtrips_unicode(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)

    messages = [{"role": "user", "content": "请帮我修复这个 bug"}]
    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = messages
    sid = save_session(agent, "model-zh")

    raw = (tmp_path / f"{sid}.json").read_bytes()
    assert "请帮我修复这个 bug".encode() in raw
    data = load_session(agent, sid)
    assert data is not None
    assert agent.messages == messages
    assert data["model"] == "model-zh"
