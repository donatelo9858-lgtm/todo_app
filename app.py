import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from models import db, User, Task, File

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///todo.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file

# Создаём папку для загрузок, если её нет
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Создание таблиц БД
with app.app_context():
    db.create_all()

# Вспомогательные функции
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'txt', 'doc', 'docx'}

# ==================== HTML СТРАНИЦЫ ====================

@app.route('/')
def index():
    if current_user.is_authenticated:
        tasks = Task.query.filter_by(user_id=current_user.id).order_by(Task.created_at.desc()).all()
        return render_template('index.html', tasks=tasks)
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if User.query.filter_by(username=username).first():
            flash('Пользователь уже существует')
            return redirect(url_for('register'))

        user = User(username=username, password=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()

        flash('Регистрация успешна! Войдите в систему.')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))

        flash('Неверное имя пользователя или пароль')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/task/create', methods=['GET', 'POST'])
@login_required
def create_task():
    if request.method == 'POST':
        title = request.form['title']
        description = request.form.get('description', '')
        deadline_str = request.form.get('deadline', '')

        deadline = None
        if deadline_str:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%d')

        task = Task(
            title=title,
            description=description,
            deadline=deadline,
            user_id=current_user.id
        )
        db.session.add(task)
        db.session.commit()

        # Обработка файла
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Добавляем префикс с id задачи, но у задачи ещё нет id?
                # Пересохраняем с правильным именем после коммита
                new_filename = f"{task.id}_{filename}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
                file.save(filepath)

                task_file = File(filename=filename, filepath=new_filename, task_id=task.id)
                db.session.add(task_file)
                db.session.commit()

        flash('Задача создана!')
        return redirect(url_for('index'))

    return render_template('task_detail.html', task=None)

@app.route('/task/<int:task_id>')
@login_required
def view_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.user_id != current_user.id:
        flash('Доступ запрещён')
        return redirect(url_for('index'))
    return render_template('task_detail.html', task=task)

@app.route('/task/<int:task_id>/complete')
@login_required
def complete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.user_id == current_user.id:
        task.status = 'completed'
        db.session.commit()
        flash('Задача выполнена!')
    return redirect(url_for('index'))

@app.route('/task/<int:task_id>/delete')
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.user_id == current_user.id:
        # Удаляем связанные файлы
        for file in task.files:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filepath)
            if os.path.exists(filepath):
                os.remove(filepath)
            db.session.delete(file)
        db.session.delete(task)
        db.session.commit()
        flash('Задача удалена')
    return redirect(url_for('index'))

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/task/<int:task_id>/add_file', methods=['POST'])
@login_required
def add_file(task_id):
    task = Task.query.get_or_404(task_id)
    if task.user_id != current_user.id:
        return jsonify({'error': 'Access denied'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400

    file = request.files['file']
    if file and file.filename and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        new_filename = f"{task.id}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        file.save(filepath)

        task_file = File(filename=filename, filepath=new_filename, task_id=task.id)
        db.session.add(task_file)
        db.session.commit()

        return jsonify({'success': True, 'filename': filename})

    return jsonify({'error': 'Invalid file'}), 400

# ==================== REST API ====================

@app.route('/api/tasks', methods=['GET'])
@login_required
def api_get_tasks():
    tasks = Task.query.filter_by(user_id=current_user.id).all()
    return jsonify([{
        'id': t.id,
        'title': t.title,
        'description': t.description,
        'status': t.status,
        'deadline': t.deadline.isoformat() if t.deadline else None,
        'created_at': t.created_at.isoformat()
    } for t in tasks])

@app.route('/api/tasks', methods=['POST'])
@login_required
def api_create_task():
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({'error': 'Title required'}), 400

    task = Task(
        title=data['title'],
        description=data.get('description', ''),
        user_id=current_user.id
    )
    db.session.add(task)
    db.session.commit()

    return jsonify({'id': task.id, 'message': 'Task created'}), 201

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
@login_required
def api_delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.user_id != current_user.id:
        return jsonify({'error': 'Access denied'}), 403

    db.session.delete(task)
    db.session.commit()
    return jsonify({'message': 'Deleted'}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)