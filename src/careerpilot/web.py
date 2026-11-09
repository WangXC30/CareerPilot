"""The one product frontend: Vite assets with bounded SPA navigation fallback."""

import os
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class WebApp(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Asset misses must stay 404. Only extensionless browser navigations
            # receive the application; API/downloads are outside this mount.
            accept = dict(scope.get("headers", [])).get(b"accept", b"")
            if (
                exc.status_code != 404
                or Path(path).suffix
                or path.split("/")[0] == "assets"
                or b"text/html" not in accept
            ):
                raise
            return await super().get_response("index.html", scope)


def mount_web(app):
    root = Path(os.environ.get("CAREERPILOT_WEB_ROOT", Path(__file__).parent / "web_dist"))

    @app.get("/app/github-callback.html", include_in_schema=False)
    def github_callback():
        if not (root / "index.html").is_file():
            raise HTTPException(503, "Frontend is unavailable")
        return FileResponse(root / "index.html")

    if (root / "index.html").is_file():
        app.mount("/app", WebApp(directory=root, html=True), name="workspace")
    else:

        @app.get("/app/{path:path}", include_in_schema=False)
        def unavailable(path: str):
            raise HTTPException(503, "Build the React frontend before starting the API")

    # The use notice is a document, never a second product frontend.
    @app.get("/local-use", include_in_schema=False)
    def local_notice():
        return FileResponse(Path(__file__).parent / "static" / "local-use.html")

    @app.get("/local-use.css", include_in_schema=False)
    def local_notice_style():
        return FileResponse(Path(__file__).parent / "static" / "local-use.css")
