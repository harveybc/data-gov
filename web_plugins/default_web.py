"""AdminLTE dashboard for lakes, storage, warnings, and per-lake logs."""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


class Plugin:
    plugin_params = {
        "web_host": "127.0.0.1",
        "web_port": 5055,
        "secret_key": "change-me-in-config",
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._context = None

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def create_app(self, context):
        self._context = context
        app = Flask(
            __name__,
            template_folder=str(Path(__file__).resolve().parent / "templates"),
        )
        app.secret_key = self.params.get("secret_key") or "change-me-in-config"
        self._register(app)
        return app

    def serve(self, context):
        app = self.create_app(context)
        host = self.params.get("web_host") or "127.0.0.1"
        port = int(self.params.get("web_port") or 5055)
        print(f"data-gov UI → http://{host}:{port}  (demo / demo)")
        app.run(host=host, port=port, debug=False)
        return 0

    def _register(self, app: Flask):
        plugin = self

        def current_user():
            return session.get("user")

        def login_required(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if not current_user():
                    return redirect(url_for("login"))
                return view(*args, **kwargs)

            return wrapped

        def plugins():
            return plugin._context["plugins"]

        @app.context_processor
        def inject():
            return {"fmt_bytes": _fmt_bytes, "user": current_user()}

        @app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "POST":
                user = plugins()["authn"].authenticate(
                    request.form.get("username") or "",
                    request.form.get("password") or "",
                )
                decision = "allow" if user else "deny"
                plugins()["accounting"].record(
                    actor=request.form.get("username"),
                    verb="authn",
                    decision=decision,
                    warning=None if user else "login failed",
                )
                if user:
                    session["user"] = user
                    return redirect(url_for("dashboard"))
                flash("Invalid credentials.", "danger")
            return render_template("login.html")

        @app.route("/logout")
        def logout():
            session.clear()
            return redirect(url_for("login"))

        @app.route("/")
        @login_required
        def dashboard():
            user = current_user()
            lakes_map = plugins()["lakes"]
            allowed = plugins()["authz"].allowed_lake_ids(user, lakes_map)
            cards = []
            for lake_id in allowed:
                lake = lakes_map[lake_id]
                storage = lake.storage()
                cards.append(
                    {
                        "id": lake_id,
                        "meta": lake.describe(),
                        "storage": storage,
                        "requests": plugins()["accounting"].request_count(lake_id),
                        "resources": len(plugins()["inventory"].resources(lake_id)),
                    }
                )
            warnings = plugins()["accounting"].warnings()
            return render_template(
                "dashboard.html",
                cards=cards,
                warnings=warnings,
                total_requests=sum(c["requests"] for c in cards),
                total_resources=sum(c["resources"] for c in cards),
            )

        @app.route("/lakes/<lake_id>")
        @login_required
        def lake_detail(lake_id):
            user = current_user()
            lakes_map = plugins()["lakes"]
            if lake_id not in lakes_map or not plugins()["authz"].can(user, lake_id):
                flash("Lake not available.", "warning")
                return redirect(url_for("dashboard"))
            lake = lakes_map[lake_id]
            return render_template(
                "lake.html",
                meta=lake.describe(),
                storage=lake.storage(),
                resources=plugins()["inventory"].resources(lake_id),
                logs=plugins()["accounting"].logs(lake_id),
                by_verb=plugins()["accounting"].stats_by_verb(lake_id),
                by_actor=plugins()["accounting"].stats_by_actor(lake_id),
            )

        return app
