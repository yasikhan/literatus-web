from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from urllib.parse import urlparse
from sqlalchemy import func
from datetime import datetime
import requests
import requests.adapters
import urllib3
import secrets
import os
import socket
import random
import re
import time
import xml.etree.ElementTree as ET
from flask_migrate import Migrate

# Create a session that forces IPv4 for Open Library (their IPv6 is broken)
ol_session = requests.Session()
class IPv4HTTPAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs['socket_options'] = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)]
        super().init_poolmanager(*args, **kwargs)

    def send(self, request, **kwargs):
        old_getaddrinfo = socket.getaddrinfo
        def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
            return old_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        socket.getaddrinfo = _ipv4_only
        try:
            return super().send(request, **kwargs)
        finally:
            socket.getaddrinfo = old_getaddrinfo

ol_session.mount('https://openlibrary.org', IPv4HTTPAdapter())

app = Flask(__name__)

uri = os.getenv("DATABASE_URL")  # or other relevant config var
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = uri or 'sqlite:///literatus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8MB upload limit

UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads', 'avatars')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'log in to continue'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=True)
    password_hash = db.Column(db.Text)
    profile_image = db.Column(db.Text)
    reading_goal = db.Column(db.Integer, nullable=True)
    books = db.relationship('Book', backref='user', lazy=True)

    @property
    def avatar_url(self):
        if self.profile_image and not self.profile_image.startswith('http'):
            return url_for('static', filename=self.profile_image)
        elif self.profile_image:
            return self.profile_image
        return f"https://api.dicebear.com/6.x/initials/svg?seed={self.username}"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def beloved_books(self):
        return [book for book in self.books if book.sentiment == 'beloved']

    @property
    def tolerated_books(self):
        return [book for book in self.books if book.sentiment == 'tolerated']

    @property
    def disliked_books(self):
        return [book for book in self.books if book.sentiment == 'disliked']


class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(100), nullable=False)
    sentiment = db.Column(db.String(20), nullable=True)
    position = db.Column(db.Integer, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    google_books_url = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='read')
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    cover_url = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(20), nullable=True, default='fiction')


CATEGORIES = ['fiction', 'non-fiction', 'memoir', 'poetry']


def detect_category(subjects):
    """Map Google Books categories or Open Library subjects to our categories."""
    if not subjects:
        return 'fiction'
    text = ' '.join(subjects).lower()
    if any(kw in text for kw in ['poetry', 'poems', 'verse', 'poet']):
        return 'poetry'
    if any(kw in text for kw in ['memoir', 'biography', 'autobiography', 'personal narrative']):
        return 'memoir'
    if any(kw in text for kw in ['fiction', 'novel', 'thriller', 'mystery', 'fantasy',
                                  'science fiction', 'romance', 'horror', 'literary',
                                  'suspense', 'adventure', 'dystopi']):
        return 'fiction'
    return 'non-fiction'


def parse_goodreads_user_id(url):
    """Extract numeric user ID from a Goodreads profile URL."""
    match = re.search(r'/user/show/(\d+)', url or '')
    return match.group(1) if match else None


def fetch_goodreads_books(user_id):
    """Fetch rated books from a Goodreads user's read shelf via RSS."""
    books = []
    page = 1
    while True:
        url = f"https://www.goodreads.com/review/list_rss/{user_id}?shelf=read&per_page=200&page={page}"
        try:
            resp = requests.get(url, timeout=15, headers={'User-Agent': 'Literatus/1.0'})
            resp.raise_for_status()
        except Exception:
            break
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            break
        items = root.findall('.//item')
        if not items:
            break
        for item in items:
            title = (item.findtext('title') or '').strip()
            author = (item.findtext('author_name') or '').strip()
            rating = (item.findtext('user_rating') or '0').strip()
            cover = (item.findtext('book_image_url') or '').strip()
            if not title or not author:
                continue
            rating_int = int(rating) if rating.isdigit() else 0
            if rating_int == 0:
                continue
            if 'nophoto' in cover:
                cover = ''
            books.append({
                'title': title[:200],
                'author': author[:100],
                'rating': rating_int,
                'cover_url': cover or None,
            })
        if len(items) < 200:
            break
        page += 1
    return books


def map_goodreads_rating(rating):
    """Map a 1-5 star rating to a sentiment."""
    if rating >= 4:
        return 'beloved'
    elif rating == 3:
        return 'tolerated'
    else:
        return 'disliked'


def lookup_book_category(title, author, author_cache=None):
    """Look up a book's category via Google Books or Open Library."""
    if author_cache is not None and author in author_cache:
        return author_cache[author]

    category = 'fiction'
    # Try Google Books
    try:
        google_key = os.environ.get('GOOGLE_BOOKS_API_KEY', '')
        gurl = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{requests.utils.quote(title)}+inauthor:{requests.utils.quote(author)}&maxResults=1"
        if google_key:
            gurl += f"&key={google_key}"
        resp = requests.get(gurl, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('items', [])
            if items:
                cats = items[0].get('volumeInfo', {}).get('categories', [])
                if cats:
                    category = detect_category(cats)
                    if author_cache is not None:
                        author_cache[author] = category
                    return category
    except Exception:
        pass

    # Fallback to Open Library
    try:
        ol_url = f"https://openlibrary.org/search.json?title={requests.utils.quote(title)}&author={requests.utils.quote(author)}&limit=1"
        resp = ol_session.get(ol_url, timeout=10)
        if resp.status_code == 200:
            docs = resp.json().get('docs', [])
            if docs:
                subjects = docs[0].get('subject', [])[:10]
                if subjects:
                    category = detect_category(subjects)
    except Exception:
        pass

    if author_cache is not None:
        author_cache[author] = category
    return category


def import_goodreads_books(user_id, books, detect_categories=True):
    """Import a list of Goodreads books for a user. Returns (imported_count, skipped_count)."""
    # Get existing books for dedup (case-insensitive)
    existing = Book.query.filter_by(user_id=user_id, status='read').all()
    existing_set = {(b.title.lower(), b.author.lower()) for b in existing}

    author_cache = {}
    new_books = []
    skipped = 0

    for book_data in books:
        key = (book_data['title'].lower(), book_data['author'].lower())
        if key in existing_set:
            skipped += 1
            continue
        existing_set.add(key)  # prevent dupes within import

        sentiment = map_goodreads_rating(book_data['rating'])
        if detect_categories:
            category = lookup_book_category(book_data['title'], book_data['author'], author_cache)
            time.sleep(0.15)
        else:
            category = 'fiction'

        new_books.append({
            'title': book_data['title'],
            'author': book_data['author'],
            'sentiment': sentiment,
            'category': category,
            'rating': book_data['rating'],
            'cover_url': book_data['cover_url'],
        })

    # Group by (sentiment, category) and assign positions
    groups = {}
    for book in new_books:
        key = (book['sentiment'], book['category'])
        groups.setdefault(key, []).append(book)

    for (sentiment, category), group_books in groups.items():
        max_pos = db.session.query(func.max(Book.position)).filter_by(
            user_id=user_id, sentiment=sentiment, category=category
        ).scalar() or 0

        # Sort by stars desc, shuffle within same star tier
        group_books.sort(key=lambda b: -b['rating'])
        i = 0
        while i < len(group_books):
            j = i
            while j < len(group_books) and group_books[j]['rating'] == group_books[i]['rating']:
                j += 1
            tier = group_books[i:j]
            random.shuffle(tier)
            group_books[i:j] = tier
            i = j

        for idx, book_data in enumerate(group_books):
            new_book = Book(
                title=book_data['title'],
                author=book_data['author'],
                sentiment=sentiment,
                category=category,
                position=max_pos + idx + 1,
                user_id=user_id,
                status='read',
                date_added=datetime.utcnow(),
                cover_url=book_data['cover_url'],
            )
            db.session.add(new_book)

    db.session.commit()
    return len(new_books), skipped


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/')
def home():
    if current_user.is_authenticated:
        community_favorites = db.session.query(
            Book.title, Book.author,
            func.count(Book.id).label('fav_count')
        ).filter(
            Book.sentiment == 'beloved', Book.status == 'read'
        ).group_by(
            Book.title, Book.author
        ).order_by(
            func.count(Book.id).desc()
        ).limit(8).all()

        want_to_read_books = Book.query.filter_by(
            user_id=current_user.id, status='want_to_read'
        ).order_by(Book.date_added.desc()).all()

        current_year = datetime.utcnow().year
        books_read_this_year = Book.query.filter(
            Book.user_id == current_user.id,
            Book.status == 'read',
            db.extract('year', Book.date_added) == current_year
        ).count()

        return render_template('home.html',
            community_favorites=community_favorites,
            want_to_read_books=want_to_read_books,
            books_read_this_year=books_read_this_year,
            reading_goal=current_user.reading_goal,
            current_year=current_year
        )
    return render_template('home.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if not username or len(username) < 3 or len(username) > 80:
            flash('Username must be between 3 and 80 characters.')
            return redirect(url_for('register'))

        if len(password) < 8:
            flash('Password must be at least 8 characters.')
            return redirect(url_for('register'))

        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists')
            return redirect(url_for('register'))

        profile_image = f"https://api.dicebear.com/6.x/initials/svg?seed={username}"
        new_user = User(username=username, profile_image=profile_image)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        goodreads_url = request.form.get('goodreads_url', '').strip()
        gr_user_id = parse_goodreads_user_id(goodreads_url) if goodreads_url else None

        if gr_user_id:
            login_user(new_user)
            books = fetch_goodreads_books(gr_user_id)
            if books:
                imported, _ = import_goodreads_books(new_user.id, books, detect_categories=True)
                flash(f'Welcome! Imported {imported} books from Goodreads.')
            else:
                flash('Welcome! Could not find rated books on that Goodreads profile.')
            return redirect(url_for('profile', username=new_user.username))

        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('profile', username=user.username))
        flash('Invalid username or password')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/profile/<username>')
@login_required
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()

    categories_data = {}
    first_beloved = None
    for cat in CATEGORIES:
        beloved = Book.query.filter_by(user_id=user.id, sentiment='beloved', category=cat).order_by(Book.position).all()
        tolerated = Book.query.filter_by(user_id=user.id, sentiment='tolerated', category=cat).order_by(Book.position).all()
        disliked = Book.query.filter_by(user_id=user.id, sentiment='disliked', category=cat).order_by(Book.position).all()

        all_cat_books = beloved + tolerated + disliked
        for i, book in enumerate(all_cat_books):
            if book.sentiment == 'beloved':
                base, max_rating = 7.5, 10
            elif book.sentiment == 'tolerated':
                base, max_rating = 4.5, 7
            else:
                base, max_rating = 1, 4

            sentiment_books = beloved if book in beloved else tolerated if book in tolerated else disliked
            sentiment_position = sentiment_books.index(book)
            sentiment_total = len(sentiment_books)
            book.rating = base + ((max_rating - base) * (1 - (sentiment_position / (sentiment_total - 1 or 1))))
            book.rating = round(book.rating, 1)
            book.global_position = i + 1

        if not first_beloved and beloved:
            first_beloved = beloved[0]

        categories_data[cat] = {
            'beloved': beloved,
            'tolerated': tolerated,
            'disliked': disliked,
            'total': len(all_cat_books)
        }

    want_to_read = Book.query.filter_by(user_id=user.id, status='want_to_read').order_by(Book.date_added.desc()).all()

    return render_template('profile.html', user=user,
                           categories=CATEGORIES,
                           categories_data=categories_data,
                           first_beloved=first_beloved,
                           want_to_read=want_to_read,
                           is_own_profile=current_user.is_authenticated and current_user.id == user.id)


@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        if len(display_name) > 100:
            flash('Display name must be under 100 characters.')
            return redirect(url_for('edit_profile'))
        current_user.display_name = display_name or None

        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename and allowed_file(file.filename):
                ext = secure_filename(file.filename).rsplit('.', 1)[1].lower()
                new_filename = f"{current_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
                file.save(os.path.join(UPLOAD_FOLDER, new_filename))
                if current_user.profile_image and not current_user.profile_image.startswith('http'):
                    old_path = os.path.join(app.static_folder, current_user.profile_image)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                current_user.profile_image = f"uploads/avatars/{new_filename}"
            elif file and file.filename:
                flash('Invalid file type. Use PNG, JPG, GIF, or WebP.')
                return redirect(url_for('edit_profile'))

        db.session.commit()
        flash('Profile updated!')
        return redirect(url_for('profile', username=current_user.username))

    return render_template('edit_profile.html')


@app.route('/import_goodreads', methods=['GET', 'POST'])
@login_required
def import_goodreads_page():
    if request.method == 'POST':
        goodreads_url = request.form.get('goodreads_url', '').strip()
        skip_categories = request.form.get('skip_categories') == 'on'

        gr_user_id = parse_goodreads_user_id(goodreads_url)
        if not gr_user_id:
            flash('Please enter a valid Goodreads profile URL.')
            return redirect(url_for('import_goodreads_page'))

        books = fetch_goodreads_books(gr_user_id)
        if not books:
            flash('No rated books found. Make sure the Goodreads profile is public.')
            return redirect(url_for('import_goodreads_page'))

        imported, skipped = import_goodreads_books(
            current_user.id, books, detect_categories=not skip_categories
        )

        if imported == 0 and skipped > 0:
            flash(f'All {skipped} books are already in your library.')
        elif skipped > 0:
            flash(f'Imported {imported} books! Skipped {skipped} already in your library.')
        else:
            flash(f'Imported {imported} books from Goodreads!')

        return redirect(url_for('profile', username=current_user.username))

    return render_template('import_goodreads.html')


@app.route('/search_users')
def search_users():
    query = request.args.get('query', '')
    if query:
        users = User.query.filter(User.username.ilike(f'%{query}%')).all()
        return render_template('search_users.html', users=users, query=query)
    return render_template('search_users.html')


@app.route('/search_books')
def search_books():
    query = request.args.get('query', '')
    if not query:
        return jsonify([])

    books = []
    headers = {'User-Agent': 'Literatus/1.0'}

    # Try Google Books first
    try:
        google_books_key = os.environ.get('GOOGLE_BOOKS_API_KEY', '')
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"
        if google_books_key:
            url += f"&key={google_books_key}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            for item in data.get('items', []):
                volume_info = item.get('volumeInfo', {})
                title = volume_info.get('title', 'Unknown Title')
                authors = volume_info.get('authors', ['Unknown Author'])
                google_books_url = volume_info.get('infoLink', '')
                cover_url = volume_info.get('imageLinks', {}).get('thumbnail', '')
                suggested_category = detect_category(volume_info.get('categories', []))
                books.append({
                    "title": title,
                    "author": authors[0],
                    "google_books_url": google_books_url,
                    "cover_url": cover_url,
                    "suggested_category": suggested_category
                })
            if books:
                return jsonify(books)
    except Exception:
        pass

    # Fallback to Open Library (using IPv4 session — their IPv6 is unreliable)
    try:
        ol_url = f"https://openlibrary.org/search.json?q={query}&limit=10"
        response = ol_session.get(ol_url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for doc in data.get('docs', []):
                if len(books) >= 5:
                    break
                langs = doc.get('language', [])
                if langs and 'eng' not in langs:
                    continue
                # Skip non-Latin titles (multilingual editions with Cyrillic/CJK etc)
                title_check = doc.get('title', '')
                if title_check and not all(c.isascii() or c in '—–\u2019\u2018\u201c\u201d' for c in title_check):
                    continue
                title = doc.get('title', 'Unknown Title')
                author_names = doc.get('author_name', ['Unknown Author'])
                cover_id = doc.get('cover_i')
                cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ''
                ol_key = doc.get('key', '')
                book_url = f"https://openlibrary.org{ol_key}" if ol_key else ''
                suggested_category = detect_category(doc.get('subject', [])[:10])
                books.append({
                    "title": title,
                    "author": author_names[0],
                    "google_books_url": book_url,
                    "cover_url": cover_url,
                    "suggested_category": suggested_category
                })
    except Exception:
        pass

    return jsonify(books)


@app.route('/add_book', methods=['POST'])
@login_required
def add_book():
    title = request.form.get('title', '').strip()
    author = request.form.get('author', '').strip()
    sentiment = request.form.get('sentiment')
    google_books_url = request.form.get('google_books_url', '').strip()

    if not all([title, author, sentiment]):
        missing_fields = [field for field in ['title', 'author', 'sentiment'] if not request.form.get(field)]
        flash(f"Error: Missing required fields: {', '.join(missing_fields)}")
        return redirect(url_for('home'))

    if len(title) > 200 or len(author) > 100:
        flash('Title or author name is too long.')
        return redirect(url_for('home'))

    category = request.form.get('category', 'fiction')
    if category not in CATEGORIES:
        category = 'fiction'

    cover_url = request.form.get('cover_url', '').strip()

    if google_books_url:
        parsed = urlparse(google_books_url)
        if parsed.scheme not in ('http', 'https') or 'google' not in parsed.netloc:
            google_books_url = None

    new_book = Book(
        title=title,
        author=author,
        sentiment=sentiment,
        user_id=current_user.id,
        position=0,
        google_books_url=google_books_url,
        status='read',
        date_added=datetime.utcnow(),
        cover_url=cover_url or None,
        category=category
    )
    db.session.add(new_book)
    db.session.commit()

    flash('Book added successfully!')
    return redirect(url_for('rate_new_book', book_id=new_book.id))


@app.route('/rate_new_book/<int:book_id>')
@login_required
def rate_new_book(book_id):
    new_book = db.session.get(Book, book_id)
    if not new_book:
        flash('Book not found.')
        return redirect(url_for('profile', username=current_user.username))

    books_to_compare = Book.query.filter_by(user_id=current_user.id, sentiment=new_book.sentiment, category=new_book.category).order_by(Book.position).all()
    books_to_compare = [book for book in books_to_compare if book.id != new_book.id]

    if not books_to_compare:
        new_book.position = 1
        db.session.commit()
        flash('Book rating completed!')
        return redirect(url_for('profile', username=current_user.username))

    session['books_to_compare'] = [book.id for book in books_to_compare]
    session['comparison_index'] = len(books_to_compare) // 2
    session['new_book_id'] = new_book.id

    compared_book = books_to_compare[session['comparison_index']]
    return render_template('rate_new_book.html', new_book=new_book, compared_book=compared_book)


@app.route('/compare_books', methods=['POST'])
@login_required
def compare_books():
    new_book_id = session.get('new_book_id')
    compared_book_id = int(request.form['compared_book_id'])
    preference = int(request.form['preference'])

    new_book = db.session.get(Book, new_book_id)
    compared_book = db.session.get(Book, compared_book_id)

    if not new_book or not compared_book:
        flash('Error: Book not found.')
        return redirect(url_for('profile', username=current_user.username))

    books_to_compare = [db.session.get(Book, book_id) for book_id in session['books_to_compare']]
    index = session['comparison_index']

    if preference == 1:  # New book is preferred
        books_to_compare = books_to_compare[:index]
    else:  # Compared book is preferred
        books_to_compare = books_to_compare[index+1:]

    if not books_to_compare:
        # We've found the position for the new book
        insert_position = compared_book.position if preference == 1 else compared_book.position + 1
        insert_book(new_book, insert_position)
        flash('Book rating completed!')
        return redirect(url_for('profile', username=current_user.username))

    session['books_to_compare'] = [book.id for book in books_to_compare]
    session['comparison_index'] = len(books_to_compare) // 2

    next_book = books_to_compare[session['comparison_index']]
    return render_template('rate_new_book.html', new_book=new_book, compared_book=next_book)


@app.route('/delete_book/<int:book_id>', methods=['POST'])
@login_required
def delete_book(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id:
        flash('You do not have permission to delete this book.')
        return redirect(url_for('profile', username=current_user.username))

    # Remove the book
    db.session.delete(book)

    # Update positions of remaining books in the same sentiment category
    remaining_books = Book.query.filter_by(user_id=current_user.id, sentiment=book.sentiment, category=book.category).filter(
        Book.position > book.position).all()
    for remaining_book in remaining_books:
        remaining_book.position -= 1

    db.session.commit()
    flash('Book deleted successfully.')
    return redirect(url_for('profile', username=current_user.username))


@app.route('/add_want_to_read', methods=['POST'])
@login_required
def add_want_to_read():
    title = request.form.get('title', '').strip()
    author = request.form.get('author', '').strip()
    google_books_url = request.form.get('google_books_url', '').strip()
    cover_url = request.form.get('cover_url', '').strip()

    if not title or not author:
        return jsonify({"success": False, "error": "Title and author required"}), 400

    if google_books_url:
        parsed = urlparse(google_books_url)
        if parsed.scheme not in ('http', 'https') or 'google' not in parsed.netloc:
            google_books_url = None

    category = request.form.get('category', 'fiction')
    if category not in CATEGORIES:
        category = 'fiction'

    new_book = Book(
        title=title,
        author=author,
        status='want_to_read',
        sentiment=None,
        position=None,
        user_id=current_user.id,
        google_books_url=google_books_url,
        cover_url=cover_url or None,
        category=category
    )
    db.session.add(new_book)
    db.session.commit()
    return jsonify({"success": True, "book_id": new_book.id, "title": title, "author": author, "category": category})


@app.route('/mark_as_read/<int:book_id>', methods=['POST'])
@login_required
def mark_as_read(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id:
        flash('Permission denied.')
        return redirect(url_for('home'))
    book.status = 'read'
    book.date_added = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('choose_sentiment', book_id=book.id))


@app.route('/choose_sentiment/<int:book_id>', methods=['GET', 'POST'])
@login_required
def choose_sentiment(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id:
        flash('Permission denied.')
        return redirect(url_for('home'))

    if request.method == 'POST':
        sentiment = request.form.get('sentiment')
        if sentiment not in ('beloved', 'tolerated', 'disliked'):
            flash('Invalid sentiment.')
            return redirect(url_for('choose_sentiment', book_id=book.id))
        book.sentiment = sentiment
        book.position = 0
        db.session.commit()
        return redirect(url_for('rate_new_book', book_id=book.id))

    return render_template('choose_sentiment.html', book=book)


@app.route('/set_reading_goal', methods=['POST'])
@login_required
def set_reading_goal():
    goal = request.form.get('goal', type=int)
    if goal and goal > 0:
        current_user.reading_goal = goal
        db.session.commit()
    return redirect(url_for('home'))


@app.route('/remove_want_to_read/<int:book_id>', methods=['POST'])
@login_required
def remove_want_to_read(book_id):
    book = Book.query.get_or_404(book_id)
    if book.user_id != current_user.id:
        flash('Permission denied.')
        return redirect(url_for('home'))
    if book.status != 'want_to_read':
        flash('This book is not on your want-to-read list.')
        return redirect(url_for('home'))
    db.session.delete(book)
    db.session.commit()
    return redirect(url_for('home'))


@app.route('/initiate_rerank/<int:book_id>')
@login_required
def initiate_rerank(book_id):
    book_to_rerank = Book.query.get_or_404(book_id)
    if book_to_rerank.user_id != current_user.id:
        flash('You do not have permission to rerank this book.')
        return redirect(url_for('profile', username=current_user.username))

    books_to_compare = Book.query.filter_by(user_id=current_user.id, sentiment=book_to_rerank.sentiment, category=book_to_rerank.category).order_by(
        Book.position).all()
    books_to_compare = [book for book in books_to_compare if book.id != book_to_rerank.id]

    if not books_to_compare:
        flash('No other books to compare for reranking.')
        return redirect(url_for('profile', username=current_user.username))

    session['books_to_compare'] = [book.id for book in books_to_compare]
    session['comparison_index'] = len(books_to_compare) // 2
    session['book_to_rerank_id'] = book_to_rerank.id

    compared_book = books_to_compare[session['comparison_index']]
    return render_template('rerank_book.html', book_to_rerank=book_to_rerank, compared_book=compared_book)


@app.route('/rerank_book', methods=['POST'])
@login_required
def rerank_book():
    book_to_rerank_id = session.get('book_to_rerank_id')
    compared_book_id = int(request.form['compared_book_id'])
    preference = int(request.form['preference'])

    book_to_rerank = Book.query.get_or_404(book_to_rerank_id)
    compared_book = Book.query.get_or_404(compared_book_id)

    books_to_compare = [Book.query.get(book_id) for book_id in session['books_to_compare']]
    index = session['comparison_index']

    if preference == 1:  # Book to rerank is preferred
        books_to_compare = books_to_compare[:index]
    else:  # Compared book is preferred
        books_to_compare = books_to_compare[index + 1:]

    if not books_to_compare:
        # We've found the new position for the book
        if preference == 1:
            if book_to_rerank.position > compared_book.position:
                new_position = compared_book.position
            else:
                new_position = book_to_rerank.position  # Maintain current position
        else:
            if book_to_rerank.position < compared_book.position:
                new_position = compared_book.position
            else:
                new_position = book_to_rerank.position  # Maintain current position
        if new_position != book_to_rerank.position:
            reposition_book(book_to_rerank, new_position)
        else: flash('Book reranking completed! The book remains in its current position.')
        flash('Book reranking completed!')
        return redirect(url_for('profile', username=current_user.username))

    session['books_to_compare'] = [book.id for book in books_to_compare]
    session['comparison_index'] = len(books_to_compare) // 2

    next_book = books_to_compare[session['comparison_index']]
    return render_template('rerank_book.html', book_to_rerank=book_to_rerank, compared_book=next_book)


def reposition_book(book, new_position):
    old_position = book.position

    if new_position == old_position:
        return

    books_to_update = Book.query.filter(
        Book.user_id == book.user_id,
        Book.sentiment == book.sentiment,
        Book.category == book.category,
        ((Book.position >= new_position) & (Book.position < old_position)) |
        ((Book.position <= new_position) & (Book.position > old_position))
    ).all()

    for update_book in books_to_update:
        if old_position < new_position:
            update_book.position -= 1
        else:
            update_book.position += 1

    book.position = new_position
    db.session.commit()


def insert_book(new_book, insert_position):
    books_to_update = Book.query.filter(
        Book.user_id == new_book.user_id,
        Book.sentiment == new_book.sentiment,
        Book.category == new_book.category,
        Book.position >= insert_position
    ).all()

    for book in books_to_update:
        book.position += 1

    new_book.position = insert_position
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run()