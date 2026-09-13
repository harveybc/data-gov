"""AdminLTE UI plus the HTTP API that service clients use."""

from __future__ import annotations

from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    jsonify,
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
        print(f"data-gov UI → http://{host}:{port}")
        app.run(host=host, port=port, debug=False)
        return 0

    def _register(self, app: Flask):
        plugin = self

        def plugins():
            return plugin._context["plugins"]

        def current_user():
            return session.get("user")

        def login_required(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if not current_user():
                    return redirect(url_for("login"))
                return view(*args, **kwargs)

            return wrapped

        def bearer_principal():
            header = request.headers.get("Authorization") or ""
            if not header.startswith("Bearer "):
                return None
            return plugins()["access"].authenticate_api_key(header[7:].strip())

        def json_error(status, message, **extra):
            payload = {"error": message}
            payload.update(extra)
            return jsonify(payload), status

        def require_service():
            principal = bearer_principal()
            if principal:
                return principal
            return None

        @app.context_processor
        def inject():
            return {"fmt_bytes": _fmt_bytes, "user": current_user()}

        @app.route("/login", methods=["GET", "POST"])
        def login():
            if request.method == "POST":
                username = request.form.get("username") or ""
                user = plugins()["access"].authenticate_password(
                    username, request.form.get("password") or ""
                )
                plugins()["accounting"].record(
                    actor=username,
                    verb="authn",
                    decision="allow" if user else "deny",
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
            allowed = plugins()["access"].allowed_lake_ids(user, lakes_map)
            cards = []
            for lake_id in allowed:
                lake = lakes_map[lake_id]
                cards.append(
                    {
                        "id": lake_id,
                        "meta": lake.describe(),
                        "storage": lake.storage(),
                        "requests": plugins()["accounting"].request_count(lake_id),
                        "resources": len(lake.discover()),
                    }
                )
            return render_template(
                "dashboard.html",
                cards=cards,
                warnings=plugins()["accounting"].warnings(),
                total_requests=sum(c["requests"] for c in cards),
                total_resources=sum(c["resources"] for c in cards),
            )

        @app.route("/lakes/<lake_id>")
        @login_required
        def lake_detail(lake_id):
            user = current_user()
            lakes_map = plugins()["lakes"]
            ok, _ = plugins()["access"].authorize(user, lake_id, "discover")
            if not ok:
                ok, _ = plugins()["access"].authorize(user, lake_id, "query")
            if lake_id not in lakes_map or not ok:
                flash("Lake not available.", "warning")
                return redirect(url_for("dashboard"))
            lake = lakes_map[lake_id]
            return render_template(
                "lake.html",
                meta=lake.describe(),
                storage=lake.storage(),
                resources=lake.discover(),
                logs=plugins()["accounting"].logs(lake_id),
                by_verb=plugins()["accounting"].stats_by_verb(lake_id),
                by_actor=plugins()["accounting"].stats_by_actor(lake_id),
            )

        def _service_or_401():
            principal = require_service()
            if principal:
                return principal, None
            plugins()["accounting"].record(
                actor=None, verb="authn", decision="deny", warning="bad api key"
            )
            return None, json_error(401, "unauthenticated")

        @app.route("/api/v1/lakes")
        def api_lakes():
            principal, err = _service_or_401()
            if err:
                return err
            lakes_map = plugins()["lakes"]
            allowed = plugins()["access"].allowed_lake_ids(principal, lakes_map)
            return jsonify(
                {
                    "lakes": [
                        lakes_map[lake_id].describe() for lake_id in allowed
                    ]
                }
            )

        @app.route("/api/v1/resources")
        def api_resources():
            principal, err = _service_or_401()
            if err:
                return err
            lake_id = request.args.get("lake")
            ok, reason = plugins()["access"].authorize(principal, lake_id, "discover")
            plugins()["accounting"].record(
                actor=principal["username"],
                lake_id=lake_id,
                verb="discover",
                decision="allow" if ok else "deny",
                warning=reason,
                experiment_key=request.headers.get("X-Experiment-Key"),
            )
            if not ok:
                return json_error(403, reason or "forbidden")
            lake = plugins()["lakes"].get(lake_id)
            if not lake:
                return json_error(404, "unknown lake")
            return jsonify({"resources": lake.discover()})

        @app.route("/api/v1/coverage")
        def api_coverage():
            principal, err = _service_or_401()
            if err:
                return err
            lake_id = request.args.get("lake")
            resource = request.args.get("resource")
            ok, reason = plugins()["access"].authorize(principal, lake_id, "coverage")
            if not ok:
                ok, reason = plugins()["access"].authorize(principal, lake_id, "read")
            plugins()["accounting"].record(
                actor=principal["username"],
                lake_id=lake_id,
                verb="coverage",
                resource_id=resource,
                decision="allow" if ok else "deny",
                warning=reason,
                experiment_key=request.headers.get("X-Experiment-Key"),
            )
            if not ok:
                return json_error(403, reason or "forbidden")
            lake = plugins()["lakes"][lake_id]
            try:
                return jsonify(lake.coverage(resource))
            except FileNotFoundError:
                return json_error(404, "unknown resource")

        @app.route("/api/v1/read")
        def api_read():
            principal, err = _service_or_401()
            if err:
                return err
            experiment = request.headers.get("X-Experiment-Key")
            lake_id = request.args.get("lake")
            resource = request.args.get("resource")
            start = request.args.get("from")
            end = request.args.get("to")
            if not experiment:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="read",
                    resource_id=resource,
                    decision="deny",
                    warning="missing experiment_key",
                )
                return json_error(403, "experiment_key required")
            ok, reason = plugins()["access"].authorize(
                principal, lake_id, "read", start=start, end=end
            )
            if not ok:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="read",
                    resource_id=resource,
                    decision="deny",
                    warning=reason,
                    experiment_key=experiment,
                )
                return json_error(403, reason or "forbidden")
            lake = plugins()["lakes"].get(lake_id)
            if not lake:
                return json_error(404, "unknown lake")
            try:
                payload = lake.read(resource, start=start, end=end)
            except FileNotFoundError:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="read",
                    resource_id=resource,
                    decision="deny",
                    warning="unknown resource",
                    experiment_key=experiment,
                )
                return json_error(404, "unknown resource")
            plugins()["accounting"].record(
                actor=principal["username"],
                lake_id=lake_id,
                verb="read",
                resource_id=resource,
                decision="allow",
                bytes=payload.get("bytes"),
                sha256=payload.get("sha256"),
                experiment_key=experiment,
            )
            return jsonify(payload)

        @app.route("/api/v1/query")
        def api_query():
            principal, err = _service_or_401()
            if err:
                return err
            experiment = request.headers.get("X-Experiment-Key")
            lake_id = request.args.get("lake")
            sql = request.args.get("sql")
            if not experiment:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="query",
                    decision="deny",
                    warning="missing experiment_key",
                )
                return json_error(403, "experiment_key required")
            ok, reason = plugins()["access"].authorize(principal, lake_id, "query")
            if not ok:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="query",
                    decision="deny",
                    warning=reason,
                    experiment_key=experiment,
                )
                return json_error(403, reason or "forbidden")
            lake = plugins()["lakes"].get(lake_id)
            try:
                payload = lake.query(sql)
            except ValueError as exc:
                return json_error(400, str(exc))
            except PermissionError:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="query",
                    decision="deny",
                    warning="holdout",
                    experiment_key=experiment,
                )
                return json_error(403, "holdout")
            plugins()["accounting"].record(
                actor=principal["username"],
                lake_id=lake_id,
                verb="query",
                decision="allow",
                bytes=payload.get("bytes"),
                sha256=payload.get("sha256"),
                experiment_key=experiment,
            )
            return jsonify(payload)

        @app.route("/api/v1/experiments/<experiment_key>/usage")
        def api_usage(experiment_key):
            principal, err = _service_or_401()
            if err:
                return err
            rows = plugins()["accounting"].usage(experiment_key)
            return jsonify({"events": rows})

        return app
