"""
VisionAI Eye Hospital - Production-Grade Backend
Version 8.0 - Local AI Inference with EfficientNetB0 + Supabase

Features:
- Local AI inference (EfficientNetB0 for eye disease detection — no API needed)
- User authentication (Username/Password + Google OAuth)
- Google Sheets logging for user registrations
- Protected routes for eye screening
- Session management with Flask-Login
- Supabase (PostgreSQL) persistent user database
"""

import os
import hashlib
import secrets
from supabase import create_client, Client
import tempfile
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # Load .env file before reading env vars

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import requests
import json

# AI Model - Local Inference
import torch
from transformers import AutoImageProcessor, AutoModelForImageClassification
from PIL import Image

# Google OAuth & Sheets
from authlib.integrations.flask_client import OAuth
import gspread
from google.oauth2.service_account import Credentials

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))
    LOCAL_MODEL_ID = 'NeuronZero/EyeDiseaseClassifier'  # BEiT-based — 96.4% accuracy, 4 classes (Cataract, DR, Glaucoma, Normal)
    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp'}
    
    # Google OAuth Configuration — set in .env file
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    
    # Google Sheets Configuration
    GOOGLE_SHEETS_CREDENTIALS_FILE = os.environ.get('GOOGLE_SHEETS_CREDENTIALS', 'credentials.json')
    GOOGLE_SHEET_NAME = 'Eye Screening Project'
    
    # Supabase Database
    SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
    SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')


# ═══════════════════════════════════════════════════════════════════════════════
# APP INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
app.config.from_object(Config)
CORS(app)

# Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access eye screening features.'
login_manager.login_message_category = 'info'

# Custom unauthorized handler for API requests
@login_manager.unauthorized_handler
def unauthorized_callback():
    """Return JSON for AJAX requests, redirect for regular requests."""
    if request.headers.get('Accept', '').find('application/json') != -1 or \
       request.content_type == 'multipart/form-data' or \
       request.is_json:
        return jsonify({'success': False, 'error': 'Authentication required. Please login.'}), 401
    return redirect(url_for('login'))

# OAuth Setup
oauth = OAuth(app)
if Config.GOOGLE_CLIENT_ID:
    google = oauth.register(
        name='google',
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )
else:
    google = None

# Create upload folder
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# Initialize Supabase client
supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
print(f"✅ Supabase client initialized: {Config.SUPABASE_URL}")

# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL AI MODEL INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

print(f"🧠 Loading AI model: {Config.LOCAL_MODEL_ID}")
print("   (First run will download ~20MB. Subsequent runs use cache.)")
try:
    image_processor = AutoImageProcessor.from_pretrained(Config.LOCAL_MODEL_ID)
    eye_model = AutoModelForImageClassification.from_pretrained(Config.LOCAL_MODEL_ID)
    eye_model.eval()  # Set to inference mode (no training)
    MODEL_LOADED = True
    print(f"✅ AI model loaded successfully!")
    print(f"   Classes: {list(eye_model.config.id2label.values())}")
except Exception as e:
    print(f"❌ Failed to load AI model: {e}")
    print("   The /predict endpoint will not work until the model is available.")
    image_processor = None
    eye_model = None
    MODEL_LOADED = False

# ═══════════════════════════════════════════════════════════════════════════════
# SUPABASE USER DATABASE (Persistent cloud storage)
# ═══════════════════════════════════════════════════════════════════════════════

class User(UserMixin):
    def __init__(self, id, email, name, phone=None, password_hash=None, login_method='password', 
                 role='patient', active=True, last_login=None, created_at=None):
        self.id = id
        self.email = email
        self.name = name
        self.phone = phone
        self.password_hash = password_hash
        self.login_method = login_method
        self.role = role
        self._active = active  # use _active to avoid conflict with Flask-Login's is_active property
        self.last_login = last_login or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.created_at = created_at or datetime.now()

    @property
    def is_active(self):
        """Override Flask-Login's is_active to use our _active field."""
        return self._active

    def check_password(self, password):
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False

def save_user_to_db(user):
    """Save a user to Supabase."""
    try:
        response = supabase.table('users').upsert({
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'phone': user.phone,
            'password_hash': user.password_hash,
            'login_method': user.login_method,
            'role': user.role,
            'is_active': user.is_active,
            'last_login': user.last_login,
            'created_at': user.created_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(user.created_at, datetime) else user.created_at
        }).execute()
        print(f"✅ User saved to Supabase: {user.email} | Response: {response.data}")
    except Exception as e:
        print(f"❌ SUPABASE INSERT FAILED for {user.email}: {e}")

def get_user_by_id(user_id):
    """Retrieve a user by ID from Supabase."""
    result = supabase.table('users').select('*').eq('id', user_id).execute()
    if result.data:
        row = result.data[0]
        return User(
            id=row['id'], email=row['email'], name=row['name'],
            phone=row.get('phone'),
            password_hash=row.get('password_hash'), 
            login_method=row.get('login_method', 'password'),
            role=row.get('role', 'patient'),
            active=row.get('is_active', True),
            last_login=row.get('last_login'),
            created_at=row.get('created_at')
        )
    return None

def get_user_by_email(email):
    """Retrieve a user by email from Supabase."""
    result = supabase.table('users').select('*').eq('email', email).execute()
    if result.data:
        row = result.data[0]
        return User(
            id=row['id'], email=row['email'], name=row['name'],
            phone=row.get('phone'),
            password_hash=row.get('password_hash'), 
            login_method=row.get('login_method', 'password'),
            role=row.get('role', 'patient'),
            active=row.get('is_active', True),
            last_login=row.get('last_login'),
            created_at=row.get('created_at')
        )
    return None

def get_next_user_id(prefix='user'):
    """Generate the next user ID by counting existing users."""
    result = supabase.table('users').select('id', count='exact').execute()
    count = result.count or 0
    return f"{prefix}_{count + 1}"

@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)

# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_sheets_client():
    """Initialize Google Sheets client with service account credentials."""
    try:
        creds_file = Config.GOOGLE_SHEETS_CREDENTIALS_FILE
        if not os.path.exists(creds_file):
            print(f"⚠️ Google Sheets credentials file not found: {creds_file}")
            return None
        
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_file(creds_file, scopes=scopes)
        client = gspread.authorize(credentials)
        return client
    except Exception as e:
        print(f"⚠️ Google Sheets initialization error: {e}")
        return None

def get_or_create_sheet():
    """Get or create the Eye Screening Project spreadsheet."""
    client = get_sheets_client()
    if not client:
        return None, None
    
    try:
        # Try to open existing sheet
        spreadsheet = client.open(Config.GOOGLE_SHEET_NAME)
        worksheet = spreadsheet.sheet1
        return spreadsheet, worksheet
    except gspread.SpreadsheetNotFound:
        # Create new spreadsheet
        try:
            spreadsheet = client.create(Config.GOOGLE_SHEET_NAME)
            worksheet = spreadsheet.sheet1
            
            # Set up headers
            headers = ['Serial No', 'Full Name', 'Email/Username', 'Login Method', 'Created At']
            worksheet.update('A1:E1', [headers])
            
            # Format headers
            worksheet.format('A1:E1', {
                'backgroundColor': {'red': 0.055, 'green': 0.647, 'blue': 0.914},
                'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}
            })
            
            print(f"✅ Created new Google Sheet: {Config.GOOGLE_SHEET_NAME}")
            return spreadsheet, worksheet
        except Exception as e:
            print(f"⚠️ Error creating spreadsheet: {e}")
            return None, None
    except Exception as e:
        print(f"⚠️ Error accessing spreadsheet: {e}")
        return None, None

def log_user_to_sheets(user):
    """Log new user registration to Google Sheets."""
    try:
        _, worksheet = get_or_create_sheet()
        if not worksheet:
            print("⚠️ Could not access Google Sheet for logging")
            return False
        
        # Get current row count for serial number
        all_values = worksheet.get_all_values()
        serial_no = len(all_values)  # Header is row 1, so this gives us the next number
        
        # Prepare row data
        row_data = [
            serial_no,
            user.name,
            user.email,
            user.login_method,
            user.created_at.strftime('%Y-%m-%d %H:%M:%S')
        ]
        
        # Append row
        worksheet.append_row(row_data)
        print(f"✅ Logged user to Google Sheets: {user.email}")
        return True
    except Exception as e:
        print(f"⚠️ Error logging to Google Sheets: {e}")
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/login')
def login():
    """Render login page."""
    if current_user.is_authenticated:
        return redirect(url_for('screening'))
    return render_template('login.html')

@app.route('/auth/register', methods=['POST'])
def register():
    """Handle user registration with username/password."""
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()
    
    # Validation
    if not email or not password or not name:
        return jsonify({'success': False, 'error': 'All fields are required'}), 400
    
    if len(password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters'}), 400
    
    # Check if user exists
    if get_user_by_email(email):
        return jsonify({'success': False, 'error': 'Email already registered'}), 400
    
    # Create user
    user_id = get_next_user_id('user')
    password_hash = generate_password_hash(password)
    
    user = User(
        id=user_id,
        email=email,
        name=name,
        password_hash=password_hash,
        login_method='Password'
    )
    
    save_user_to_db(user)
    
    # Log to Google Sheets
    log_user_to_sheets(user)
    
    # Login user
    login_user(user)
    
    return jsonify({
        'success': True,
        'message': 'Account created successfully',
        'user': {'name': user.name, 'email': user.email}
    })

@app.route('/auth/login', methods=['POST'])
def login_post():
    """Handle user login with username/password."""
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    # Find user in SQLite
    user = get_user_by_email(email)
    
    if not user or not user.check_password(password):
        return jsonify({'success': False, 'error': 'Invalid email or password'}), 401
    
    login_user(user)
    
    return jsonify({
        'success': True,
        'message': 'Login successful',
        'user': {'name': user.name, 'email': user.email}
    })

@app.route('/auth/google')
def google_login():
    """Initiate Google OAuth flow."""
    if not google:
        flash('Google Sign-In is not configured', 'error')
        return redirect(url_for('login'))
    
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    """Handle Google OAuth callback."""
    if not google:
        flash('Google Sign-In is not configured', 'error')
        return redirect(url_for('login'))
    
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            user_info = google.get('https://www.googleapis.com/oauth2/v3/userinfo').json()
        
        email = user_info.get('email', '').lower()
        name = user_info.get('name', email.split('@')[0])
        
        # Check if user exists in SQLite
        existing_user = get_user_by_email(email)
        
        if existing_user:
            login_user(existing_user)
        else:
            # Create new user
            user_id = get_next_user_id('google')
            
            user = User(
                id=user_id,
                email=email,
                name=name,
                login_method='Google'
            )
            
            save_user_to_db(user)
            
            # Log to Google Sheets
            log_user_to_sheets(user)
            
            login_user(user)
        
        return redirect(url_for('screening'))
    
    except Exception as e:
        print(f"Google OAuth error: {e}")
        flash('Google Sign-In failed. Please try again.', 'error')
        return redirect(url_for('login'))

@app.route('/auth/logout')
@login_required
def logout():
    """Handle user logout."""
    logout_user()
    return redirect(url_for('home'))

@app.route('/auth/status')
def auth_status():
    """Check authentication status."""
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'user': {
                'name': current_user.name,
                'email': current_user.email,
                'login_method': current_user.login_method
            }
        })
    return jsonify({'authenticated': False})

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    """Landing page - accessible to everyone."""
    return render_template('index.html', user=current_user if current_user.is_authenticated else None)

@app.route('/screening')
@login_required
def screening():
    """Eye screening page - protected route."""
    return render_template('screening.html', user=current_user)

@app.route('/predict', methods=['POST'])
@login_required
def predict():
    """AI prediction endpoint - local inference with EfficientNetB0."""
    if not MODEL_LOADED:
        return jsonify({'success': False, 'error': 'AI model is not loaded. Please restart the server.'}), 503
    
    if 'image' not in request.files:
        return jsonify({'success': False, 'error': 'No image uploaded'}), 400
    
    file = request.files['image']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'Empty file'}), 400
    
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in Config.ALLOWED_EXTENSIONS:
        return jsonify({'success': False, 'error': f'Invalid format. Use: {", ".join(Config.ALLOWED_EXTENSIONS)}'}), 400
    
    saved_path = None
    try:
        # Save file temporarily for processing
        filename = secure_filename(file.filename)
        saved_path = os.path.join(Config.UPLOAD_FOLDER, filename)
        file.save(saved_path)
        
        print(f"🧠 Processing image locally: {filename}")
        
        # Open image and run local inference
        image = Image.open(saved_path).convert('RGB')
        inputs = image_processor(images=image, return_tensors='pt')
        
        with torch.no_grad():
            outputs = eye_model(**inputs)
        
        # Get probabilities via softmax
        logits = outputs.logits
        probs = torch.softmax(logits, dim=-1)[0]
        
        # Build predictions list sorted by confidence
        predictions = []
        for i in range(len(probs)):
            label = eye_model.config.id2label.get(i, f'Class {i}')
            confidence = round(probs[i].item() * 100, 2)
            predictions.append({'label': label, 'confidence': confidence})
        
        predictions.sort(key=lambda x: x['confidence'], reverse=True)
        
        top = predictions[0]
        print(f"✅ Prediction: {top['label']} ({top['confidence']}% confidence)")
        
        return jsonify({
            'success': True,
            'predictions': predictions,
            'model': Config.LOCAL_MODEL_ID,
            'inference': 'local',
            'user': current_user.name
        })
    
    except Exception as e:
        print(f"❌ Exception in predict: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        # Cleanup: remove uploaded file after processing
        if saved_path and os.path.exists(saved_path):
            try:
                os.remove(saved_path)
                print(f"🧹 Cleaned up uploaded file: {saved_path}")
            except OSError as cleanup_err:
                print(f"⚠️ Failed to cleanup file {saved_path}: {cleanup_err}")

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'model': Config.LOCAL_MODEL_ID,
        'model_loaded': MODEL_LOADED,
        'inference': 'local',
        'google_oauth_configured': bool(Config.GOOGLE_CLIENT_ID),
        'google_sheets_configured': os.path.exists(Config.GOOGLE_SHEETS_CREDENTIALS_FILE)
    })

@app.route('/config')
def get_config():
    """Public configuration endpoint."""
    return jsonify({
        'model': Config.LOCAL_MODEL_ID,
        'inference': 'local',
        'model_loaded': MODEL_LOADED,
        'maxFileSize': Config.MAX_CONTENT_LENGTH,
        'allowedFormats': list(Config.ALLOWED_EXTENSIONS),
        'googleOAuthEnabled': bool(Config.GOOGLE_CLIENT_ID)
    })

# ═══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(401)
def unauthorized(e):
    if request.is_json or request.path.startswith('/api'):
        return jsonify({'success': False, 'error': 'Authentication required'}), 401
    return redirect(url_for('login'))

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
