from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import os
from scraper import fetch_schedules_html, parse_schedules_html
from scraper import fetch_scorecard_html, parse_scorecard_html


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    CORS(app)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/schedules/raw")
    def api_raw():
        try:
            html_text = fetch_schedules_html()
            return jsonify({"ok": True, "html": html_text})
        except Exception as error:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/schedules")
    def api_schedules():
        try:
            html_text = fetch_schedules_html()
            items = parse_schedules_html(html_text)
            return jsonify({"ok": True, "count": len(items), "items": items})
        except Exception as error:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/scorecard/raw")
    def api_scorecard_raw():
        try:
            url = request.args.get("url")
            if not url:
                return jsonify({"ok": False, "error": "missing url"}), 400
            html_text = fetch_scorecard_html(url)
            return jsonify({"ok": True, "html": html_text})
        except Exception as error:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(error)}), 500

    @app.route("/api/scorecard")
    def api_scorecard():
        try:
            url = request.args.get("url")
            if not url:
                return jsonify({"ok": False, "error": "missing url"}), 400
            html_text = fetch_scorecard_html(url)
            data = parse_scorecard_html(html_text)
            return jsonify(data)
        except Exception as error:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(error)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)