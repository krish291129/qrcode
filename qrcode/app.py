import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

# ------------------
# Configuration
# ------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
QR_FOLDER = os.path.join(BASE_DIR, 'static', 'qr')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-this')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['QR_FOLDER'] = QR_FOLDER

db = SQLAlchemy(app)

# ------------------
# Models
# ------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    albums = db.relationship('Album', backref='owner', lazy=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    qr_path = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    photos = db.relationship('Photo', backref='album', lazy=True)


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey('album.id'), nullable=False)
    filename = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# ------------------
# Helpers
# ------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_qr_for_album(album_id):
    album_url = url_for('view_album', album_id=album_id, _external=True)
    qr_img = qrcode.make(album_url)
    qr_filename = f'qr_album_{album_id}.png'
    qr_path = os.path.join(app.config['QR_FOLDER'], qr_filename)
    qr_img.save(qr_path)
    return f'qr/{qr_filename}'


# ------------------
# Create DB on Start (Flask 3 compatible)
# ------------------
with app.app_context():
    db.create_all()


# ------------------
# Routes
# ------------------

@app.route('/')
def index():
    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
    return render_template('index.html', user=user)

# ----- Auth -----
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name').strip()
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')

        if not (name and email and password):
            flash('Please fill all fields', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'warning')
            return redirect(url_for('register'))

        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('Registration successful. Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email').strip().lower()
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash('Invalid credentials', 'danger')
            return redirect(url_for('login'))

        session['user_id'] = user.id
        flash('Logged in successfully', 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

# ----- Dashboard -----
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    albums = Album.query.filter_by(user_id=user.id).order_by(Album.created_at.desc()).all()

    return render_template('dashboard.html', user=user, albums=albums)


# ----- Create Album & Upload Photos -----
@app.route('/album/create', methods=['GET','POST'])
def create_album():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        album_name = request.form.get('album_name') or f'Album-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}'
        user_id = session['user_id']

        album = Album(name=album_name, user_id=user_id)
        db.session.add(album)
        db.session.commit()

        # create album folder
        album_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(album.id))
        os.makedirs(album_dir, exist_ok=True)

        files = request.files.getlist('photos')
        for f in files:
            if f and allowed_file(f.filename):
                filename = secure_filename(f.filename)
                save_path = os.path.join(album_dir, filename)
                f.save(save_path)

                photo = Photo(album_id=album.id, filename=filename)
                db.session.add(photo)

        db.session.commit()

        # generate QR
        qr_rel_path = generate_qr_for_album(album.id)
        album.qr_path = qr_rel_path
        db.session.commit()

        flash('Album created successfully', 'success')
        return redirect(url_for('dashboard'))

    return render_template('create_album.html')


# ----- View Album -----
@app.route('/album/view/<int:album_id>')
def view_album(album_id):
    album = Album.query.get_or_404(album_id)
    album_dir_rel = f'uploads/{album.id}'

    photos = [
        {'url': url_for('static', filename=f'{album_dir_rel}/{p.filename}'), 'name': p.filename}
        for p in album.photos
    ]

    qr_url = url_for('static', filename=album.qr_path) if album.qr_path else None

    return render_template('view_album.html', album=album, photos=photos, qr_url=qr_url)


# ----- Download QR -----
@app.route('/qr/download/<int:album_id>')
def download_qr(album_id):
    album = Album.query.get_or_404(album_id)

    if not album.qr_path:
        flash('QR not found', 'warning')
        return redirect(url_for('dashboard'))

    qr_file = os.path.basename(album.qr_path)
    return send_from_directory(app.config['QR_FOLDER'], qr_file, as_attachment=True)


# ----- Delete Album -----
@app.route('/album/delete/<int:album_id>', methods=['POST'])
def delete_album(album_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    album = Album.query.get_or_404(album_id)

    if album.user_id != session['user_id']:
        flash('Not authorized', 'danger')
        return redirect(url_for('dashboard'))

    # delete photos
    album_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(album.id))
    if os.path.exists(album_dir):
        for fname in os.listdir(album_dir):
            os.remove(os.path.join(album_dir, fname))
        os.rmdir(album_dir)

    # delete qr
    if album.qr_path:
        qr_file = os.path.join(app.config['QR_FOLDER'], os.path.basename(album.qr_path))
        if os.path.exists(qr_file):
            os.remove(qr_file)

    # database cleanup
    Photo.query.filter_by(album_id=album.id).delete()
    db.session.delete(album)
    db.session.commit()

    flash('Album deleted', 'info')
    return redirect(url_for('dashboard'))


# ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

