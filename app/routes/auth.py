import re
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.models.user import User
from app import db

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validations
        if not username or not email or not password:
            flash('All fields are required.', 'danger')
            return render_template('register.html')
            
        if len(username) < 3 or len(username) > 30:
            flash('Username must be between 3 and 30 characters.', 'danger')
            return render_template('register.html')
            
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            flash('Please enter a valid email address.', 'danger')
            return render_template('register.html')
            
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html')
            
        # Check existing
        existing_username = User.query.filter_by(username=username).first()
        if existing_username:
            flash('Username already exists. Choose another one.', 'danger')
            return render_template('register.html')
            
        existing_email = User.query.filter_by(email=email).first()
        if existing_email:
            flash('Email address already registered.', 'danger')
            return render_template('register.html')
            
        # Create User
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            flash('An error occurred during registration. Please try again.', 'danger')
            
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
        
    if request.method == 'POST':
        login_input = request.form.get('login_input', '').strip() # can be email or username
        password = request.form.get('password', '')
        remember = True if request.form.get('remember') else False
        
        if not login_input or not password:
            flash('Please fill in all fields.', 'danger')
            return render_template('login.html')
            
        # Search by username or email
        user = User.query.filter((User.username == login_input) | (User.email == login_input)).first()
        
        if not user or not user.check_password(password):
            flash('Invalid username/email or password.', 'danger')
            return render_template('login.html')
            
        # Successful login
        login_user(user, remember=remember)
        flash(f'Welcome back, {user.username}!', 'success')
        
        next_page = request.args.get('next')
        # Simple security check to prevent open redirects
        if not next_page or not next_page.startswith('/'):
            next_page = url_for('main.dashboard')
            
        return redirect(next_page)
        
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('main.index'))
