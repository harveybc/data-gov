"""AdminLTE UI plus the HTTP API that service clients use."""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.report import canonical_body, canonical_json, report_sha256
from app.governance import (
    HEX64_RE as GOV_HEX64_RE,
    canonical_json as governance_json,
    object_sha256 as governance_sha256,
    normalise_campaign,
    normalise_confirmation,
    normalise_terminal,
)
from lake_plugins.errors import LakeUnreachable, UnsupportedError

CHUNK = 1024 * 1024
MAX_BODY = 16 * 1024 * 1024
RETRY_AFTER = "30"
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
LINEAGE_FIELDS = ("lineage", "reason", "event_id")


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _day(text):
    if not isinstance(text, str) or not DAY_RE.match(text):
        raise ValueError("invalid from/to")
    try:
        return _dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid from/to") from exc


def validate_range(start, end):
    """Both calendar days and from <= to, else ValueError('invalid from/to')."""
    if _day(end) < _day(start):
        raise ValueError("invalid from/to")


def _number(value, name):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if not math.isfinite(out):
        raise ValueError("non-finite value")
    return out


def _integer(value, name):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if not math.isfinite(out) or out != int(out):
        raise ValueError(f"invalid {name}")
    return int(out)


def _text(value, name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"invalid {name}")
    return value


def _normalise_metric(item):
    if not isinstance(item, dict):
        raise ValueError("invalid metric")
    name = item.get("metric")
    if not isinstance(name, str) or not name:
        raise ValueError("invalid metric")
    return {
        "metric": name,
        "value": _number(item.get("value"), "value"),
        "split": _text(item.get("split"), "split"),
        "horizon": _integer(item.get("horizon"), "horizon"),
        "std_dev": _number(item.get("std_dev"), "std_dev"),
        "min_value": _number(item.get("min_value"), "min_value"),
        "max_value": _number(item.get("max_value"), "max_value"),
        "unit": _text(item.get("unit"), "unit"),
    }


def _normalise_dataset(item):
    if not isinstance(item, dict):
        raise ValueError("invalid dataset")
    lake = item.get("lake")
    resource = item.get("resource")
    sha256 = item.get("sha256")
    if not isinstance(lake, str) or not lake or not isinstance(resource, str) or not resource:
        raise ValueError("invalid dataset")
    if not isinstance(sha256, str) or not HEX64_RE.match(sha256):
        raise ValueError("invalid dataset sha256")
    return {
        "lake": lake,
        "resource": resource,
        "sha256": sha256.lower(),
        "role": _text(item.get("role"), "role"),
    }


def normalise_report(payload, experiment_key, actor, lake_ids):
    """Validate and normalise a metrics body (04_FLOW_V2 §3). ValueError carries the 400 text."""
    lake = payload.get("lake")
    if not isinstance(lake, str) or lake not in lake_ids:
        raise ValueError("unknown lake")
    set_key = payload.get("experiment_set_key")
    if set_key is not None and (not isinstance(set_key, str) or not KEY_RE.match(set_key)):
        raise ValueError("invalid key")
    tags = payload.get("tags")
    if tags is None:
        tags = {}
    if not isinstance(tags, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in tags.items()
    ):
        raise ValueError("invalid tags")
    metrics = payload.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("metrics required")
    datasets = payload.get("datasets")
    if datasets is None:
        datasets = []
    if not isinstance(datasets, list):
        raise ValueError("invalid datasets")
    unique = {}
    for item in datasets:
        entry = _normalise_dataset(item)
        unique.setdefault(
            (entry["lake"], entry["resource"], entry["sha256"], entry["role"]), entry
        )
    return {
        "experiment_key": experiment_key,
        "experiment_set_key": set_key,
        "actor": actor,
        "lake": lake,
        "config_sha256": _text(payload.get("config_sha256"), "config_sha256"),
        "code_commit": _text(payload.get("code_commit"), "code_commit"),
        "project": _text(payload.get("project"), "project"),
        "phase": _text(payload.get("phase"), "phase"),
        "tags": tags,
        "datasets": list(unique.values()),
        "metrics": [_normalise_metric(m) for m in metrics],
    }


def _detail(row):
    try:
        return json.loads((row or {}).get("detail") or "{}") or {}
    except ValueError:
        return {}


class Plugin:
    plugin_params = {
        "web_host": "127.0.0.1",
        "web_port": 5055,
        "secret_key": "change-me-in-config",
        "max_downloads": 2,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._context = None
        self._slots = None

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def create_app(self, context):
        self._context = context
        self._slots = threading.BoundedSemaphore(int(self.params.get("max_downloads") or 2))
        here = Path(__file__).resolve().parent
        app = Flask(
            __name__,
            template_folder=str(here / "templates"),
            static_folder=str(here / "static"),
            static_url_path="/static",
        )
        app.secret_key = self.params.get("secret_key") or "change-me-in-config"
        app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
        self._register(app)
        return app

    def serve(self, context):
        app = self.create_app(context)
        host = self.params.get("web_host") or "127.0.0.1"
        port = int(self.params.get("web_port") or 5055)
        print(f"data-gov UI → http://{host}:{port}")
        print("credentials: var/credentials.json  (not in git)")
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
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

        def deny(status, warning, **row):
            """Write the deny row and answer; the warning is the error text."""
            plugins()["accounting"].record(decision="deny", warning=warning, **row)
            return json_error(status, row.pop("error", None) or warning)

        @app.context_processor
        def inject():
            return {"fmt_bytes": _fmt_bytes, "user": current_user()}

        @app.route("/healthz")
        def healthz():
            return "ok\n", 200, {"Content-Type": "text/plain"}

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
            creds = (
                Path(__file__).resolve().parents[1] / "var" / "credentials.json"
            )
            return render_template(
                "login.html",
                credentials_exist=creds.is_file(),
            )

        @app.route("/logout")
        def logout():
            session.clear()
            return redirect(url_for("login"))

        @app.route("/settings", methods=["GET", "POST"])
        @login_required
        def settings():
            import secrets
            from app.operator_config import editable_config, pending_config, persist_pending, destination

            if current_user().get("role") not in {"ceo", "data_engineer"}:
                return "Operator role required", 403
            config = plugin._context["config"]
            session.setdefault("settings_csrf", secrets.token_urlsafe(32))
            error = None
            saved = False
            text = json.dumps(editable_config(config), indent=2)
            if request.method == "POST":
                text = request.form.get("configuration", "")
                if not secrets.compare_digest(request.form.get("csrf", ""), session["settings_csrf"]):
                    error = "Reload the configuration form before saving."
                else:
                    try:
                        candidate = pending_config(config, text)
                        persist_pending(config, candidate)
                        saved = True
                    except (ValueError, TypeError, OSError) as exc:
                        error = str(exc)
            people = [
                {"name": name, "kind": value.get("kind"), "role": value.get("role")}
                for name, value in config.get("principals", {}).items()
            ]
            return render_template(
                "settings.html", configuration=text, people=people, error=error,
                saved=saved, pending_path=str(destination(config)),
                pending_exists=destination(config).is_file(),
            ), (400 if error else 200)

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
            except UnsupportedError as exc:
                return json_error(422, str(exc))

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
            row = dict(
                actor=principal["username"],
                lake_id=lake_id,
                verb="read",
                resource_id=resource,
                experiment_key=experiment,
            )
            if not experiment:
                return deny(
                    403, "missing experiment_key", error="experiment_key required", **row
                )
            if not KEY_RE.match(experiment):
                return deny(400, "invalid key", **row)
            if not start or not end:
                return deny(400, "from and to are required", **row)
            try:
                validate_range(start, end)
            except ValueError as exc:
                return deny(400, str(exc), **row)
            ok, reason = plugins()["access"].authorize(
                principal, lake_id, "read", start=start, end=end
            )
            if not ok:
                return deny(403, reason or "forbidden", **row)
            lake = plugins()["lakes"].get(lake_id)
            if not lake:
                return json_error(404, "unknown lake")
            try:
                payload = lake.read(resource, start=start, end=end)
            except PermissionError as exc:
                return deny(403, str(exc) or "holdout", **row)
            except FileNotFoundError:
                return deny(404, "unknown resource", **row)
            except ValueError as exc:
                return deny(400, str(exc) or "invalid from/to", **row)
            except UnsupportedError as exc:
                return deny(422, str(exc), **row)
            except RuntimeError as exc:
                return deny(503, str(exc), **row)
            plugins()["accounting"].record(
                decision="allow",
                bytes=payload.get("bytes"),
                sha256=payload.get("sha256"),
                **row,
            )
            return jsonify(payload)

        @app.route("/api/v1/download")
        def api_download():
            principal, err = _service_or_401()
            if err:
                return err
            experiment = request.headers.get("X-Experiment-Key")
            lake_id = request.args.get("lake")
            resource = request.args.get("resource")
            start = request.args.get("from")
            end = request.args.get("to")
            row = dict(
                actor=principal["username"],
                lake_id=lake_id,
                verb="download",
                resource_id=resource,
                experiment_key=experiment,
            )
            if not experiment:
                return deny(
                    403, "missing experiment_key", error="experiment_key required", **row
                )
            if not KEY_RE.match(experiment):
                return deny(400, "invalid key", **row)
            if (start is None) != (end is None):
                return deny(400, "invalid from/to", **row)
            if start is not None:
                try:
                    validate_range(start, end)
                except ValueError as exc:
                    return deny(400, str(exc), **row)
            ok, reason = plugins()["access"].authorize(
                principal, lake_id, "download", start=start, end=end
            )
            if not ok:
                return deny(403, reason or "forbidden", **row)
            lake = plugins()["lakes"].get(lake_id)
            if lake is None:
                return deny(404, "unknown lake", **row)
            if not hasattr(lake, "download"):
                return deny(404, "lake does not serve files", **row)
            slots = plugin._slots
            if not slots.acquire(blocking=False):
                response, status = deny(503, "download slots busy", **row)
                response.headers["Retry-After"] = RETRY_AFTER
                return response, status
            handed_off = False
            try:
                try:
                    info = lake.download(resource, start=start, end=end)
                except PermissionError as exc:
                    return deny(403, str(exc) or "holdout", **row)
                except FileNotFoundError:
                    return deny(404, "unknown resource", **row)
                except ValueError as exc:
                    return deny(400, str(exc) or "invalid from/to", **row)
                except UnsupportedError as exc:
                    return deny(422, str(exc), **row)
                except RuntimeError as exc:
                    return deny(503, str(exc) or "lake unreachable", **row)
                path = Path(info["path"])
                try:
                    handle = open(path, "rb")
                except OSError as exc:
                    return deny(503, f"lake file unreadable: {exc.strerror}", **row)
                if info.get("spool"):
                    path.unlink(missing_ok=True)
                released = threading.Event()

                def release():
                    if not released.is_set():
                        released.set()
                        handle.close()
                        slots.release()

                try:
                    digest, size = _hash_handle(handle)
                    handle.seek(0)
                    if info.get("sha256") and digest != str(info["sha256"]).lower():
                        release()
                        handed_off = True
                        return deny(503, "lake hash mismatch", **row)
                    detail = {
                        "from": start,
                        "to": end,
                        "source_sha256": info.get("source_sha256"),
                        "delivery": info.get("delivery"),
                        "time_column": info.get("time_column") or "",
                    }
                    _source_changed_check(lake_id, resource, detail, row)
                    plugins()["accounting"].record(
                        decision="allow",
                        bytes=size,
                        sha256=digest,
                        detail=json.dumps(detail, sort_keys=True),
                        **row,
                    )
                except BaseException:
                    release()
                    handed_off = True
                    raise

                def body():
                    try:
                        while True:
                            chunk = handle.read(CHUNK)
                            if not chunk:
                                break
                            yield chunk
                    finally:
                        release()

                response = Response(
                    body(), mimetype="application/octet-stream", direct_passthrough=True
                )
                response.call_on_close(release)
                filename = str(info.get("filename") or path.name)
                filename = filename.replace('"', "").replace("\r", "").replace("\n", "")
                response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
                response.headers["Content-Length"] = str(size)
                response.headers["X-Content-SHA256"] = digest
                response.headers["X-Source-SHA256"] = str(info.get("source_sha256") or "")
                response.headers["X-Delivery"] = str(info.get("delivery") or "")
                response.headers["X-Time-Column"] = str(info.get("time_column") or "")
                handed_off = True
                return response
            finally:
                if not handed_off:
                    slots.release()

        def _campaign_for_request(campaign_sha256, actor):
            if not isinstance(campaign_sha256, str) or not GOV_HEX64_RE.fullmatch(
                campaign_sha256
            ):
                return None, (json_error(400, "invalid campaign_sha256"))
            campaign = plugins()["accounting"].campaign(campaign_sha256)
            if campaign is None:
                return None, json_error(404, "unknown campaign")
            if campaign["actor"] != actor:
                return None, json_error(403, "campaign belongs to another actor")
            return campaign, None

        @app.route("/api/v2/campaigns", methods=["POST"])
        def api_v2_campaigns():
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            payload = request.get_json(silent=True)
            try:
                campaign = normalise_campaign(payload, actor, plugins()["lakes"])
            except ValueError as exc:
                return json_error(400, str(exc))
            access = plugins()["access"]
            for dataset in campaign["datasets"]:
                ok, reason = access.authorize(
                    principal, dataset["lake"], "download",
                    start=dataset["from"], end=dataset["to"],
                )
                if not ok:
                    return json_error(403, reason or "dataset forbidden")
            ok, reason = access.authorize(
                principal, campaign["terminal_lake"], "write_terminal"
            )
            if not ok:
                return json_error(403, reason or "terminal lake forbidden")
            lake = plugins()["lakes"][campaign["terminal_lake"]]
            if not hasattr(lake, "write_terminal"):
                return json_error(400, "terminal lake does not store terminals")
            try:
                stored = plugins()["accounting"].register_campaign(
                    campaign, governance_json(campaign)
                )
            except ValueError as exc:
                return json_error(409, str(exc))
            plugins()["accounting"].record(
                actor=actor, lake_id=campaign["terminal_lake"], verb="campaign_submit",
                resource_id=campaign["campaign_key"], decision="allow",
                sha256=campaign["campaign_sha256"], experiment_key=campaign["campaign_key"],
            )
            return jsonify({
                "campaign_sha256": campaign["campaign_sha256"],
                "stored": bool(stored), "already_stored": not stored,
                "classification": campaign["classification"],
            }), (201 if stored else 200)

        @app.route("/api/v2/download")
        def api_v2_download():
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            campaign_sha = request.headers.get("X-Campaign-SHA256")
            unit_id = request.headers.get("X-Unit-ID")
            campaign, err = _campaign_for_request(campaign_sha, actor)
            if err:
                return err
            if not unit_id or unit_id not in campaign["units"]:
                return json_error(403, "unit is not declared by campaign")
            lake_id = request.args.get("lake")
            resource = request.args.get("resource")
            role = request.args.get("role")
            start = request.args.get("from")
            end = request.args.get("to")
            declared = next((item for item in campaign["datasets"] if (
                item["lake"], item["resource"], item["role"], item["from"], item["to"]
            ) == (lake_id, resource, role, start, end)), None)
            if declared is None:
                return json_error(403, "dataset request is not declared by campaign")
            ok, reason = plugins()["access"].authorize(
                principal, lake_id, "download", start=start, end=end
            )
            if not ok:
                return json_error(403, reason or "forbidden")
            lake = plugins()["lakes"].get(lake_id)
            if lake is None or not hasattr(lake, "governed_download"):
                return json_error(422, "lake has no governing delivery contract")
            if not plugin._slots.acquire(blocking=False):
                response = jsonify({"error": "download slots busy"})
                response.headers["Retry-After"] = RETRY_AFTER
                return response, 503
            handed_off = False
            handle = None
            try:
                try:
                    info = lake.governed_download(resource, start=start, end=end)
                except PermissionError as exc:
                    return json_error(403, str(exc) or "holdout")
                except FileNotFoundError:
                    return json_error(404, "unknown resource")
                except ValueError as exc:
                    return json_error(400, str(exc))
                except UnsupportedError as exc:
                    return json_error(422, str(exc))
                except RuntimeError as exc:
                    return json_error(503, str(exc))
                handle = info.get("handle")
                if handle is None:
                    return json_error(503, "lake returned no retained descriptor")
                contract_sha256 = info.get("availability_contract_sha256")
                if not isinstance(contract_sha256, str) or not GOV_HEX64_RE.fullmatch(
                    contract_sha256
                ):
                    handle.close()
                    return json_error(503, "lake returned no availability contract identity")
                delivery_id = plugins()["accounting"].create_delivery(
                    campaign_sha256=campaign_sha, unit_id=unit_id, actor=actor,
                    lake_id=lake_id, resource_id=resource, role=role,
                    range_from=start, range_to=end, sha256=info["sha256"],
                    bytes_count=info["bytes"], source_sha256=info.get("source_sha256"),
                    delivery_kind=info.get("delivery"), time_column=info.get("time_column"),
                    availability_contract_sha256=contract_sha256,
                )
                released = threading.Event()

                def release_v3():
                    if not released.is_set():
                        released.set()
                        handle.close()
                        plugin._slots.release()

                def body_v3():
                    try:
                        while True:
                            chunk = handle.read(CHUNK)
                            if not chunk:
                                break
                            yield chunk
                    finally:
                        release_v3()

                response = Response(
                    body_v3(), mimetype="application/octet-stream", direct_passthrough=True
                )
                response.call_on_close(release_v3)
                response.headers["Content-Length"] = str(info["bytes"])
                response.headers["X-Content-SHA256"] = info["sha256"]
                response.headers["X-Source-SHA256"] = str(info.get("source_sha256") or "")
                response.headers["X-Delivery"] = str(info.get("delivery") or "")
                response.headers["X-Time-Column"] = str(info.get("time_column") or "")
                response.headers["X-Availability-Contract-SHA256"] = str(
                    info.get("availability_contract_sha256") or ""
                )
                # the scope of the contract, published separately: what the label denotes,
                # the completion bound, the time-zone evidence and the use class
                scope = info.get("availability") or {}
                response.headers["X-Availability-Label"] = str(scope.get("label") or "UNKNOWN")
                response.headers["X-Availability-Completion-Lag-Max"] = str(
                    scope.get("completion_lag_max") if scope.get("completion_lag_max") is not None else ""
                )
                response.headers["X-Timezone-Evidence"] = str(scope.get("timezone_evidence") or "UNKNOWN")
                response.headers["X-Availability-Use"] = str(scope.get("use_class") or "UNDECLARED")
                response.headers["X-Delivery-ID"] = delivery_id
                response.headers["X-Campaign-SHA256"] = campaign_sha
                handed_off = True
                return response
            finally:
                if not handed_off:
                    if handle is not None and not handle.closed:
                        handle.close()
                    plugin._slots.release()

        @app.route("/api/v2/deliveries/<delivery_id>/confirm", methods=["POST"])
        def api_v2_confirm_delivery(delivery_id):
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            campaign_sha = request.headers.get("X-Campaign-SHA256")
            _, err = _campaign_for_request(campaign_sha, actor)
            if err:
                return err
            try:
                confirmation = normalise_confirmation(request.get_json(silent=True))
                state, stored = plugins()["accounting"].confirm_delivery(
                    delivery_id, campaign_sha256=campaign_sha, actor=actor,
                    sha256=confirmation["sha256"], bytes_count=confirmation["bytes"],
                    cached=confirmation["cached"],
                )
            except ValueError as exc:
                return json_error(400, str(exc))
            except LookupError as exc:
                return json_error(404, str(exc))
            except PermissionError as exc:
                return json_error(403, str(exc))
            except RuntimeError as exc:
                return json_error(409, str(exc))
            plugins()["accounting"].record(
                actor=actor, verb="delivery_verified", resource_id=delivery_id,
                decision="allow", sha256=confirmation["sha256"],
                experiment_key=campaign_sha, warning=("cache" if confirmation["cached"] else None),
            )
            return jsonify({"delivery_id": delivery_id, "state": state,
                            "stored": stored, "already_stored": not stored})

        @app.route(
            "/api/v2/campaigns/<campaign_sha>/units/<unit_id>/terminal", methods=["POST"]
        )
        def api_v2_terminal(campaign_sha, unit_id):
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            if request.headers.get("X-Campaign-SHA256") != campaign_sha:
                return json_error(400, "campaign header mismatch")
            campaign, err = _campaign_for_request(campaign_sha, actor)
            if err:
                return err
            if unit_id not in campaign["units"] or request.headers.get("X-Unit-ID") != unit_id:
                return json_error(403, "unit is not declared by campaign")
            try:
                terminal = normalise_terminal(
                    request.get_json(silent=True), campaign, unit_id, actor
                )
            except ValueError as exc:
                return json_error(400, str(exc))
            try:
                deliveries = plugins()["accounting"].verified_deliveries(
                    terminal["deliveries"], campaign_sha256=campaign_sha,
                    actor=actor, unit_id=unit_id,
                )
            except ValueError as exc:
                return json_error(422, str(exc))
            except PermissionError as exc:
                return json_error(403, str(exc))
            if terminal["status"] == "COMPLETED" and campaign["input_mode"] == "DATASETS":
                got = {(item["lake_id"], item["resource_id"], item["role"],
                        item["range_from"], item["range_to"]) for item in deliveries}
                required = {(item["lake"], item["resource"], item["role"],
                             item["from"], item["to"]) for item in campaign["datasets"]}
                if not required.issubset(got):
                    return json_error(422, "completed terminal lacks verified campaign data")
            terminal["verified_datasets"] = [{
                key: item[key]
                for key in (
                    "delivery_id", "lake_id", "resource_id", "role", "sha256",
                    "bytes", "source_sha256", "range_from", "range_to",
                    "delivery_kind", "time_column", "state",
                    "availability_contract_sha256",
                )
            } for item in deliveries]
            terminal["terminal_sha256"] = governance_sha256(terminal)
            existing = plugins()["accounting"].terminal_slot(
                campaign_sha, unit_id, terminal["generation"]
            )
            if existing:
                if existing["terminal_sha256"] != terminal["terminal_sha256"]:
                    return json_error(409, "terminal generation conflict")
                return jsonify({"terminal_sha256": terminal["terminal_sha256"],
                                "stored": False, "already_stored": True}), 200
            lake = plugins()["lakes"][campaign["terminal_lake"]]
            try:
                outcome = lake.write_terminal(terminal)
                plugins()["accounting"].record_terminal(
                    terminal, governance_json(terminal)
                )
            except ValueError as exc:
                return json_error(400, str(exc))
            except LakeUnreachable as exc:
                # transport failure, never a conflict: clients keep the envelope and retry
                return json_error(503, f"terminal lake unreachable: {exc}")
            except RuntimeError as exc:
                return json_error(409, str(exc))
            except Exception as exc:
                return json_error(503, f"terminal lake unreachable: {exc}")
            plugins()["accounting"].record(
                actor=actor, lake_id=campaign["terminal_lake"], verb="write_terminal",
                resource_id=f"campaign/{campaign_sha}/unit/{unit_id}", decision="allow",
                sha256=terminal["terminal_sha256"], experiment_key=campaign["campaign_key"],
            )
            return jsonify({
                "terminal_sha256": terminal["terminal_sha256"],
                "stored": bool(outcome.get("stored")),
                "already_stored": bool(outcome.get("already_stored")),
            }), (201 if outcome.get("stored") else 200)

        @app.route("/api/v2/campaigns/<campaign_sha>/reconcile")
        def api_v2_reconcile(campaign_sha):
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            campaign, err = _campaign_for_request(campaign_sha, actor)
            if err:
                return err
            accounting_rows = plugins()["accounting"].terminal_digests(campaign_sha)
            lake = plugins()["lakes"][campaign["terminal_lake"]]
            if not hasattr(lake, "terminal_digests"):
                return json_error(503, "terminal lake cannot reconcile")
            try:
                lake_rows = lake.terminal_digests(campaign_sha)
            except Exception as exc:
                return json_error(503, f"terminal lake unreachable: {exc}")
            accounting_set = {row["terminal_sha256"] for row in accounting_rows}
            lake_set = {row["terminal_sha256"] for row in lake_rows}
            terminal_units = {row["unit_id"] for row in accounting_rows}
            return jsonify({
                "campaign_sha256": campaign_sha,
                "missing_units": sorted(set(campaign["units"]) - terminal_units),
                "accounting_only": sorted(accounting_set - lake_set),
                "lake_only": sorted(lake_set - accounting_set),
            })

        def _hash_handle(handle):
            import hashlib

            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            return digest.hexdigest(), size

        def _source_changed_check(lake_id, resource, detail, row):
            """Compare with the last allow download of (lake, resource); a change is an event."""
            accounting = plugins()["accounting"]
            last = accounting.last_download(lake_id, resource)
            if not last:
                return
            previous = _detail(last).get("source_sha256")
            current = detail.get("source_sha256")
            if not previous or not current or previous == current:
                return
            event = {
                "kind": "source_changed",
                "lake_id": lake_id,
                "resource_id": resource,
                "previous_source_sha256": previous,
                "source_sha256": current,
                "previous_event_id": last.get("id"),
            }
            plugins()["role"].handle_event(event)
            accounting.record(
                actor=row.get("actor"),
                lake_id=lake_id,
                verb="event",
                resource_id=resource,
                decision=None,
                warning="source_changed",
                experiment_key=row.get("experiment_key"),
                detail=json.dumps(event, sort_keys=True),
            )

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
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="query",
                    decision="deny",
                    warning=str(exc),
                    experiment_key=experiment,
                )
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
            except RuntimeError as exc:
                plugins()["accounting"].record(
                    actor=principal["username"],
                    lake_id=lake_id,
                    verb="query",
                    decision="deny",
                    warning=str(exc),
                    experiment_key=experiment,
                )
                return json_error(503, str(exc))
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

        def _check_lineage(dataset, actor, keys):
            """VERIFIED with the matched event_id, else UNVERIFIED with the closest reason."""
            accounting = plugins()["accounting"]
            match = accounting.find_download(
                dataset["lake"], dataset["resource"], dataset["sha256"], actor, keys
            )
            if match:
                detail = _detail(match)
                dataset.update(
                    lineage="VERIFIED",
                    reason=None,
                    event_id=match["id"],
                    source_sha256=detail.get("source_sha256"),
                    range_from=detail.get("from"),
                    range_to=detail.get("to"),
                    delivery=detail.get("delivery"),
                    time_column=detail.get("time_column"),
                )
                return
            rows = accounting.usage_by_sha256(dataset["sha256"], limit=1000)
            same = [
                r
                for r in rows
                if r["lake_id"] == dataset["lake"] and r["resource_id"] == dataset["resource"]
            ]
            if any(r["actor"] == actor for r in same):
                reason = "not served under this key or set"
            elif same:
                reason = "different actor"
            elif rows:
                reason = "hash seen for another resource"
            else:
                reason = "never served"
            dataset.update(
                lineage="UNVERIFIED",
                reason=reason,
                event_id=None,
                source_sha256=None,
                range_from=None,
                range_to=None,
                delivery=None,
                time_column=None,
            )

        @app.route("/api/v1/experiments/<experiment_key>/metrics", methods=["POST"])
        def api_report_metrics(experiment_key):
            principal, err = _service_or_401()
            if err:
                return err
            actor = principal["username"]
            row = dict(
                actor=actor,
                verb="write_metrics",
                resource_id=f"experiment/{experiment_key}",
                experiment_key=experiment_key,
            )
            if not KEY_RE.match(experiment_key):
                return deny(400, "invalid key", **row)
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return deny(400, "invalid json", **row)
            lakes_map = plugins()["lakes"]
            try:
                report = normalise_report(payload, experiment_key, actor, lakes_map)
            except ValueError as exc:
                return deny(400, str(exc), **row)
            lake_id = report["lake"]
            row["lake_id"] = lake_id
            access = plugins()["access"]
            ok, reason = access.authorize(principal, lake_id, "write_metrics")
            if not ok:
                return deny(403, reason or "forbidden", **row)
            lake = lakes_map[lake_id]
            if not hasattr(lake, "write_metrics"):
                return deny(400, "lake does not store metrics", **row)
            keys = [experiment_key]
            if report["experiment_set_key"]:
                keys.append(report["experiment_set_key"])
            for dataset in report["datasets"]:
                _check_lineage(dataset, actor, keys)
            report["lineage"] = (
                "VERIFIED"
                if report["datasets"]
                and all(d["lineage"] == "VERIFIED" for d in report["datasets"])
                else "UNVERIFIED"
            )
            report["report_sha256"] = report_sha256(report)
            report["received_at"] = _utc_now()
            body = canonical_body(report)
            canonical = canonical_json(body)
            datasets_out = [
                {k: d.get(k) for k in ("lake", "resource", "sha256", "role", *LINEAGE_FIELDS)}
                for d in report["datasets"]
            ]
            stored_report = dict(body)
            stored_report.update(
                report_sha256=report["report_sha256"],
                received_at=report["received_at"],
                lineage=report["lineage"],
                datasets=datasets_out,
            )
            row.update(sha256=report["report_sha256"], bytes=len(canonical.encode("ascii")))
            require = access.policy_attr(
                principal, lake_id, "write_metrics", "require_lineage", True
            )
            if report["lineage"] != "VERIFIED" and require:
                response, status = deny(
                    422,
                    "unverified lineage",
                    detail=json.dumps(stored_report, sort_keys=True),
                    **row,
                )
                payload = response.get_json()
                payload.update(
                    report_sha256=report["report_sha256"],
                    lineage=report["lineage"],
                    datasets=datasets_out,
                )
                return jsonify(payload), status
            try:
                outcome = lake.write_metrics(report)
            except ValueError as exc:
                return deny(400, str(exc), **row)
            except Exception as exc:
                return deny(
                    503,
                    "lake unreachable",
                    detail=json.dumps({"exception": str(exc)}),
                    **row,
                )
            stored_report.update(
                stored=bool(outcome.get("stored")),
                already_stored=bool(outcome.get("already_stored")),
            )
            plugins()["accounting"].record(
                decision="allow",
                warning=(
                    "unverified dataset lineage" if report["lineage"] != "VERIFIED" else None
                ),
                detail=json.dumps(stored_report, sort_keys=True),
                **row,
            )
            status = 201 if outcome.get("stored") else 200
            return (
                jsonify(
                    {
                        "report_sha256": report["report_sha256"],
                        "stored": bool(outcome.get("stored")),
                        "already_stored": bool(outcome.get("already_stored")),
                        "lineage": outcome.get("lineage") or report["lineage"],
                        "datasets": datasets_out,
                    }
                ),
                status,
            )

        def _paging():
            limit = request.args.get("limit", "1000")
            before = request.args.get("before_id")
            try:
                limit = max(1, min(int(limit), 10000))
                before_id = int(before) if before not in (None, "") else None
            except ValueError as exc:
                raise ValueError("invalid limit/before_id") from exc
            return limit, before_id

        @app.route("/api/v1/experiments/<experiment_key>/usage")
        def api_usage(experiment_key):
            principal, err = _service_or_401()
            if err:
                return err
            if not KEY_RE.match(experiment_key):
                return json_error(400, "invalid key")
            try:
                limit, before_id = _paging()
            except ValueError as exc:
                return json_error(400, str(exc))
            rows = plugins()["accounting"].usage(experiment_key, limit=limit, before_id=before_id)
            return jsonify({"events": rows})

        @app.route("/api/v1/datasets/<sha256>/usage")
        def api_dataset_usage(sha256):
            principal, err = _service_or_401()
            if err:
                return err
            if not HEX64_RE.match(sha256):
                return json_error(400, "invalid sha256")
            try:
                limit, before_id = _paging()
            except ValueError as exc:
                return json_error(400, str(exc))
            rows = plugins()["accounting"].usage_by_sha256(
                sha256.lower(), limit=limit, before_id=before_id
            )
            return jsonify({"events": rows})

        return app
