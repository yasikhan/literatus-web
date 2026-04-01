# Literatus

A book-ranking web app that uses pairwise comparison instead of traditional rating scales. Rather than assigning stars or numbers, you rank books by choosing between pairs — the way you actually think about preferences.

## How It Works

1. **Categorize** a book as Beloved, Tolerated, or Disliked
2. **Compare** it against your existing books in that category through a series of A/B choices
3. **Rank** is determined automatically via binary search — each comparison halves the remaining candidates until the exact position is found

Ratings are computed from position within each sentiment range:
- Beloved: 7.5 – 10.0
- Tolerated: 4.5 – 7.0
- Disliked: 1.0 – 4.0

Books can be reranked at any time through the same pairwise process.

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-Migrate
- **Frontend:** Jinja2 templates, Tailwind CSS, jQuery
- **API:** Google Books (search and book metadata)
- **Database:** SQLite (development), PostgreSQL (production)
- **Deployment:** Heroku with Gunicorn

## Setup

```bash
git clone <repo-url>
cd literatus-web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
DATABASE_URL=sqlite:///literatus.db
SECRET_KEY=<generate-with: python -c "import secrets; print(secrets.token_hex(32))">
```

Initialize the database and run:

```bash
flask db upgrade
python app.py
```

The app will be available at `http://localhost:5000`.
