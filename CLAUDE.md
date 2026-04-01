# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Development
python app.py                          # Runs on http://localhost:5000

# Production
gunicorn app:app

# Database migrations
flask db migrate -m "description"      # Create migration
flask db upgrade                       # Apply migrations
```

## Environment Variables

- `DATABASE_URL` — connection string (defaults to `sqlite:///literatus.db`; production uses PostgreSQL)
- `SECRET_KEY` — Flask secret key (auto-generated if missing)
- `GOOGLE_BOOKS_API_KEY` — optional, enhances Google Books search

The app auto-converts `postgres://` URIs to `postgresql://` for SQLAlchemy compatibility.

## Architecture

Single-file Flask app (`app.py`) with two models:

- **User** — auth via Flask-Login, password hashing via Werkzeug, DiceBear avatar URLs
- **Book** — belongs to User, has `status` (read/want_to_read) and `sentiment` (beloved/tolerated/disliked)

### Book Ranking System

The core feature is pairwise comparison ranking, not star ratings. When a user adds a book:
1. They choose a sentiment category (beloved/tolerated/disliked)
2. Binary search drives A/B comparisons against existing books in that category
3. The book's `position` field determines its rank; a computed rating maps position to a numeric range (beloved: 7.5–10, tolerated: 4.5–7, disliked: 1–4)

Key functions: `insert_book()`, `reposition_book()`, and the `/compare_books` + `/rerank_book` routes handle the ranking logic.

### Book Search

Google Books API is the primary search source with Open Library as fallback. The Open Library integration uses an IPv4-only HTTP adapter to work around IPv6 connectivity issues.

### Frontend

Custom CSS (`static/css/sketchpad.css`) with a hand-drawn literary aesthetic — no CSS framework. Custom font: YasiHand-Regular. Templates extend `layout.html`.

## Code Style

- All routes, models, and logic live in `app.py`
- CSRF protection via Flask-WTF on all forms
- Flash messages for user-facing errors
- SQLAlchemy for all database operations
- Flask-Login for authentication

## Deployment

Heroku via Procfile (`web: gunicorn app:app`). Python version pinned in `.python-version` (3.12).
