import importlib
import sys

from fastapi.testclient import TestClient


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_DIRECTORIES", f"{tmp_path / 'sounds'},{tmp_path / 'memes'}")
    monkeypatch.setenv("STATS_DATABASE", str(tmp_path / "stats.db"))
    monkeypatch.setenv("CLIPS_DIRECTORY", str(tmp_path / "clips"))
    monkeypatch.setenv("DISCORD_TOKEN", "")
    monkeypatch.setenv("WEB_API_TOKEN", "")
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


def test_page_files_are_revalidated_but_api_responses_are_not(tmp_path, monkeypatch) -> None:
    main = load_app(tmp_path, monkeypatch)

    with TestClient(main.app) as client:
        for path in ("/", "/static/app.js", "/static/styles.css", "/static/editor.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
        # Clip audio can change under the same name when an edit replaces it.
        assert client.get("/api/clips/missing.mp3").headers["cache-control"] == "no-cache"
        assert "cache-control" not in client.get("/api/health").headers


def test_adding_a_clip_to_the_sound_library(tmp_path, monkeypatch) -> None:
    main = load_app(tmp_path, monkeypatch)
    (tmp_path / "sounds").mkdir()
    clip = main.clips.save(b"ID3 fake mp3", "mp3", "Test server general")["name"]

    with TestClient(main.app) as client:
        status = client.get("/api/settings/clip-export").json()
        assert status["target"] is None and status["subfolder"] == "clips"
        assert [root["path"] for root in status["roots"]] == [str(tmp_path / "sounds"), str(tmp_path / "memes")]
        # Not configured yet.
        assert client.post(f"/api/clips/{clip}/export", json={}).status_code == 400

        # Only configured library folders and simple subfolders are allowed.
        assert client.post("/api/settings/clip-export", json={"root": str(tmp_path)}).status_code == 400
        bad = {"root": str(tmp_path / "sounds"), "subfolder": "../../escape"}
        assert client.post("/api/settings/clip-export", json=bad).status_code == 400

        saved = client.post("/api/settings/clip-export", json={"root": str(tmp_path / "sounds"), "subfolder": "clips"}).json()
        assert saved["target"] == str(tmp_path / "sounds" / "clips")
        assert saved["writable"]

        result = client.post(f"/api/clips/{clip}/export", json={"sound_name": "Lou's big moment"}).json()
        assert result["sound_name"] == "Lou's big moment"
        assert (tmp_path / "sounds" / "clips" / "Lou's big moment.mp3").read_bytes() == b"ID3 fake mp3"
        # Playable straight away: the library was rescanned.
        sounds = client.get("/api/sounds").json()
        assert [(s["name"], s["relative_path"]) for s in sounds] == [("Lou's big moment", "clips/Lou's big moment.mp3")]

        assert client.post("/api/clips/missing.mp3/export", json={}).status_code == 404
        assert client.post("/api/settings/clip-export", json={"root": None}).json()["target"] is None


def test_read_only_library_folder_is_reported_clearly(tmp_path, monkeypatch) -> None:
    main = load_app(tmp_path, monkeypatch)
    clip = main.clips.save(b"audio", "mp3", "clip")["name"]
    monkeypatch.setattr(main, "is_writable", lambda path: False)  # like a :ro Docker mount

    with TestClient(main.app) as client:
        status = client.post("/api/settings/clip-export", json={"root": str(tmp_path / "sounds")}).json()
        assert status["target"] and not status["writable"]
        assert not any(root["writable"] for root in status["roots"])
        response = client.post(f"/api/clips/{clip}/export", json={})
        assert response.status_code == 400
        assert ":ro" in response.json()["detail"]
    assert not (tmp_path / "sounds" / "clips").exists()
