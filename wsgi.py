"""Production entry point for a WSGI server, e.g. on Render:

    python -m gunicorn wsgi:app --worker-class gthread --workers 1 --threads 8 --bind 0.0.0.0:$PORT --timeout 120

One worker with several threads: the app upgrades each school's database on first use, and a
single process means those upgrades never run twice at the same time.
"""
from app import create_app

app = create_app()
