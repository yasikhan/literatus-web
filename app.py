from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import os
from flask_migrate import Migrate

app = Flask(__name__)

uri = os.getenv("DATABASE_URL")  # or other relevant config var
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = uri or 'sqlite:///literatus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hannah_arendt_is_great')

db = SQLAlchemy(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.Text)
    profile_image = db.Column(db.Text)
    books = db.relationship('Book', backref='user', lazy=True)

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
    sentiment = db.Column(db.String(20), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    google_books_url = db.Column(db.String(256), nullable=True)  # New field for Google Books URL



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists')
            return redirect(url_for('register'))

        profile_image = f"https://api.dicebear.com/6.x/initials/svg?seed={username}"
        new_user = User(username=username, profile_image=profile_image)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

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


# @app.route('/profile')
# @login_required
# def profile():
#     beloved_books = Book.query.filter_by(user_id=current_user.id, sentiment='beloved').order_by(Book.position).all()
#     tolerated_books = Book.query.filter_by(user_id=current_user.id, sentiment='tolerated').order_by(Book.position).all()
#     disliked_books = Book.query.filter_by(user_id=current_user.id, sentiment='disliked').order_by(Book.position).all()
#
#     all_books = beloved_books + tolerated_books + disliked_books
#     total_books = len(all_books)
#
#     for i, book in enumerate(all_books):
#         if book.sentiment == 'beloved':
#             base = 7.5
#             max_rating = 10
#         elif book.sentiment == 'tolerated':
#             base = 4.5
#             max_rating = 7
#         else:  # disliked
#             base = 1
#             max_rating = 4
#
#         category_books = beloved_books if book in beloved_books else \
#             tolerated_books if book in tolerated_books else \
#                 disliked_books
#         category_position = category_books.index(book)
#         category_total = len(category_books)
#
#         book.rating = base + ((max_rating - base) * (1 - (category_position / (category_total - 1 or 1))))
#         book.rating = round(book.rating, 1)  # Round to one decimal place
#         book.global_position = i + 1
#
#     return render_template('profile.html', user=current_user,
#                            beloved_books=beloved_books,
#                            tolerated_books=tolerated_books,
#                            disliked_books=disliked_books)

@app.route('/profile/<username>')
@login_required
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()

    beloved_books = Book.query.filter_by(user_id=user.id, sentiment='beloved').order_by(Book.position).all()
    tolerated_books = Book.query.filter_by(user_id=user.id, sentiment='tolerated').order_by(Book.position).all()
    disliked_books = Book.query.filter_by(user_id=user.id, sentiment='disliked').order_by(Book.position).all()

    all_books = beloved_books + tolerated_books + disliked_books
    total_books = len(all_books)

    for i, book in enumerate(all_books):
        if book.sentiment == 'beloved':
            base = 7.5
            max_rating = 10
        elif book.sentiment == 'tolerated':
            base = 4.5
            max_rating = 7
        else:  # disliked
            base = 1
            max_rating = 4

        category_books = beloved_books if book in beloved_books else \
            tolerated_books if book in tolerated_books else \
                disliked_books
        category_position = category_books.index(book)
        category_total = len(category_books)

        book.rating = base + ((max_rating - base) * (1 - (category_position / (category_total - 1 or 1))))
        book.rating = round(book.rating, 1)  # Round to one decimal place
        book.global_position = i + 1

    return render_template('profile.html', user=user,
                           beloved_books=beloved_books,
                           tolerated_books=tolerated_books,
                           disliked_books=disliked_books,
                           is_own_profile=current_user.is_authenticated and current_user.id == user.id)


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
    if query:
        url = f"https://www.googleapis.com/books/v1/volumes?q={query}&maxResults=5"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            books = []
            for item in data.get('items', []):
                volume_info = item.get('volumeInfo', {})
                title = volume_info.get('title', 'Unknown Title')
                authors = volume_info.get('authors', ['Unknown Author'])
                google_books_url = volume_info.get('infoLink', '')  # Get the Google Books URL
                books.append({
                    "title": title,
                    "author": authors[0],
                    "google_books_url": google_books_url
                })
            return jsonify(books)
    return jsonify([])


@app.route('/add_book', methods=['POST'])
@login_required
def add_book():
    #print("Received form data:", request.form)
    title = request.form.get('title')
    author = request.form.get('author')
    sentiment = request.form.get('sentiment')
    google_books_url = request.form.get('google_books_url')  # New field

    if not all([title, author, sentiment]):
        missing_fields = [field for field in ['title', 'author', 'sentiment'] if not request.form.get(field)]
        flash(f"Error: Missing required fields: {', '.join(missing_fields)}")
        return redirect(url_for('search_books'))

    new_book = Book(
        title=title,
        author=author,
        sentiment=sentiment,
        user_id=current_user.id,
        position=0,
        google_books_url=google_books_url  # Add the Google Books URL
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

    books_to_compare = Book.query.filter_by(user_id=current_user.id, sentiment=new_book.sentiment).order_by(Book.position).all()
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
    remaining_books = Book.query.filter_by(user_id=current_user.id, sentiment=book.sentiment).filter(
        Book.position > book.position).all()
    for remaining_book in remaining_books:
        remaining_book.position -= 1

    db.session.commit()
    flash('Book deleted successfully.')
    return redirect(url_for('profile', username=current_user.username))


@app.route('/initiate_rerank/<int:book_id>')
@login_required
def initiate_rerank(book_id):
    book_to_rerank = Book.query.get_or_404(book_id)
    if book_to_rerank.user_id != current_user.id:
        flash('You do not have permission to rerank this book.')
        return redirect(url_for('profile', username=current_user.username))

    books_to_compare = Book.query.filter_by(user_id=current_user.id, sentiment=book_to_rerank.sentiment).order_by(
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
        Book.position >= insert_position
    ).all()

    for book in books_to_update:
        book.position += 1

    new_book.position = insert_position
    db.session.commit()


def update_ratings(user_id, sentiment):
    books = Book.query.filter_by(user_id=user_id, sentiment=sentiment).order_by(Book.position).all()
    total_books = len(books)
    for index, book in enumerate(books, start=1):
        book.rating = 10 - ((index - 1) * 9 / (total_books - 1)) if total_books > 1 else 10
    db.session.commit()


def update_category_ratings(books):
    if not books:
        return
    min_rating = min(book.rating or 5 for book in books)
    max_rating = max(book.rating or 5 for book in books)
    for book in books:
        if min_rating != max_rating:
            normalized_rating = (book.rating - min_rating) / (max_rating - min_rating)
            if book.sentiment == 'beloved':
                book.rating = 7 + (normalized_rating * 3)
            elif book.sentiment == 'tolerated':
                book.rating = 4 + (normalized_rating * 3)
            else:  # disliked
                book.rating = 1 + (normalized_rating * 3)
        else:
            if book.sentiment == 'beloved':
                book.rating = 8.5
            elif book.sentiment == 'tolerated':
                book.rating = 5.5
            else:  # disliked
                book.rating = 2.5


def binary_search_insert(books, new_book):
    left, right = 0, len(books) - 1
    while left <= right:
        mid = (left + right) // 2
        if books[mid].rating < new_book.rating:
            right = mid - 1
        else:
            left = mid + 1
    return left


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run()