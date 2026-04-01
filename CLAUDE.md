# Literatus Web - Development Guide

## Build & Run Commands
- Run development server: `python app.py`
- Run with Gunicorn (production): `gunicorn app:app`
- Database migration commands:
  - Initialize migrations: `flask db init`
  - Create migration: `flask db migrate -m "description"`
  - Apply migrations: `flask db upgrade`

## Code Style Guidelines
- **Imports**: Group imports by standard library, third-party, and local modules
- **Naming**: 
  - Classes: PascalCase (e.g., `User`, `Book`)
  - Functions/variables: snake_case (e.g., `update_ratings`)
  - Constants: UPPER_SNAKE_CASE
- **Error Handling**: Use flash messages for user-facing errors
- **Database**: Use SQLAlchemy for all database operations
- **Authentication**: Utilize Flask-Login for user auth functionality

## Project Structure
- Flask application with SQLAlchemy ORM
- Database models in app.py (User and Book)
- Templates in `/templates` using Jinja2
- Static assets in `/static`