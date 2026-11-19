import os
import secrets
from datetime import timedelta

from flask import Flask, jsonify, render_template, request

import config
from portal import db, etl
from portal.api import api_bp
from portal.auth import init_auth
from portal.views import main_bp


def _secret_key():
    key = os.environ.get("PORTAL_SECRET_KEY")
    if key:
        return key
    os.makedirs(os.path.dirname(config.SECRET_KEY_FILE), exist_ok=True)
    if not os.path.exists(config.SECRET_KEY_FILE):
        with open(config.SECRET_KEY_FILE, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(config.SECRET_KEY_FILE) as fh:
        return fh.read().strip()


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.HTTPS_ONLY_COOKIES,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if config.BEHIND_PROXY:
        # trust the proxy's X-Forwarded-For/Proto so login throttling sees real client IPs
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_db()
    app.teardown_appcontext(db.close_db)

    init_auth(app)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.context_processor
    def refresh_info():
        conn = db.get_db()
        return {
            "last_refresh": db.get_meta(conn, "last_refresh"),
            "refresh_status": db.get_meta(conn, "refresh_status"),
        }

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def http_error(e):
        code = getattr(e, "code", 500)
        message = getattr(e, "description", "Something went wrong.")
        if code == 500:
            app.logger.exception("Server error")
        if request.path.startswith("/api/"):
            return jsonify({"error": message}), code
        return render_template("error.html", code=code, message=message), code

    return app


app = create_app()

if __name__ == "__main__":
    # Development server only. For real use: python serve.py
    etl.start_scheduler()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False, port=config.PORT)
