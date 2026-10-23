import asyncio
import hashlib
import re

import httpx

from app.main import create_app


def test_fresh_database_and_local_page_resources(test_settings):
    async def scenario():
        assert not test_settings.database_path.exists()
        app = create_app(test_settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                health = await client.get("/api/health")
                assert health.json()["database"]["status"] == "ready"
                projects = await client.get("/api/project-versions")
                assert projects.status_code == 200
                assert projects.json() == []
                templates = await client.get("/api/templates/checklists")
                assert templates.status_code == 200
                assert templates.json()
                for url in ("/", "/static/app.js", "/static/api.js", "/static/styles.css"):
                    resource = await client.get(url)
                    assert resource.status_code == 200, url
                    assert not re.search(r"https?://", resource.text), url
                runtime = await client.get("/static/vendor/alpine-3.14.9.min.js")
                assert runtime.status_code == 200
                assert hashlib.sha256(runtime.content).hexdigest() == (
                    "3ed1eed252488921df65e363d6715deb04d7f92aaedb9e52199fdf73cb1e0ad3"
                )
        # Restart with the same database: migrations and seed loading are repeatable.
        async with app.router.lifespan_context(app):
            assert test_settings.database_path.is_file()
    asyncio.run(scenario())
