"""Production entry point:  python serve.py   (uses waitress, no debug mode)."""
import config
from app import app
from portal import etl

if __name__ == "__main__":
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit("waitress is not installed. Run:  pip install -r requirements.txt")

    etl.start_scheduler()
    print(f"Kitting Portal running on http://{config.HOST}:{config.PORT}  "
          f"(data refresh at {', '.join(config.REFRESH_TIMES)})")
    serve(app, host=config.HOST, port=config.PORT, threads=config.THREADS)
