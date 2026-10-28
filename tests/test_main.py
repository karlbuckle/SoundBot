import importlib
import sys

from fastapi.testclient import TestClient


def test_page_files_are_revalidated_but_api_responses_are_not(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIO_DIRECTORIES", str(tmp_path / "sounds"))
    monkeypatch.setenv("STATS_DATABASE", str(tmp_path / "stats.db"))
    monkeypatch.setenv("CLIPS_DIRECTORY", str(tmp_path / "clips"))
    monkeypatch.setenv("DISCORD_TOKEN", "")
    monkeypatch.setenv("WEB_API_TOKEN", "")
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")

    with TestClient(main.app) as client:
        for path in ("/", "/static/app.js", "/static/styles.css", "/static/editor.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
        # Clip audio can change under the same name when an edit replaces it.
        assert client.get("/api/clips/missing.mp3").headers["cache-control"] == "no-cache"
        assert "cache-control" not in client.get("/api/health").headers
