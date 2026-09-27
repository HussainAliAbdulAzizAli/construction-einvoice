from flask import Flask, request, jsonify, send_file, send_from_directory, session, render_template, redirect, url_for
from flask_cors import CORS
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from ocr_processor import OCRProcessor
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import json
from datetime import datetime, timedelta
import uuid
import mysql.connector
from mysql.connector import Error
from functools import wraps
import hashlib
import re
from PIL import Image
import base64
from io import BytesIO
import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.abspath(os.path.join(BASE_DIR, '..', 'frontend'))
frontend_templates = frontend_dir

app = Flask(__name__, template_folder=frontend_templates, static_folder=BASE_DIR, static_url_path='/static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default-dev-key-change-me')


@app.before_request
def block_attackers():
    """Block common attack patterns before they reach routes"""
    blocked_paths = [
        'wp-admin', 'wp-login', 'wp-includes', 'xmlrpc', 
        '.env', '.git', 'phpinfo', 'config.php', 'php-cgi',
        'actuator', '.aws', 'vendor', 'wp-json'
    ]
    blocked_extensions = ['.php', '.asp', '.jsp', '.cgi']
    
    path = request.path.lower()
    

    for blocked in blocked_paths:
        if blocked in path:
            return "Forbidden - Access denied", 403
    

    for ext in blocked_extensions:
        if path.endswith(ext):
            return "Forbidden - Access denied", 403
    

    user_agent = request.headers.get('User-Agent', '')
    if not user_agent or user_agent == '':
        return "Forbidden - Access denied", 403



app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


PRODUCTION = os.environ.get('PRODUCTION', 'True').lower() == 'true'

if PRODUCTION:
    CORS(app, supports_credentials=True, origins=['https://invoicestruct.com', 'https://www.invoicestruct.com'])
    app.config.update(
        PREFERRED_URL_SCHEME='https',
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(days=7)
    )
else:
    CORS(app, supports_credentials=True)


DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'invoice_system'),
    'charset': 'utf8mb4'
}


SMTP_HOST = 'mail.privateemail.com'
SMTP_PORT = 587
SMTP_USER = os.environ.get('SMTP_USER', 'info@invoicestruct.com')
SMTP_PASS = os.environ.get('SMTP_PASSWORD', '')

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'pdf'}
MAX_FILE_SIZE = 16 * 1024 * 1024

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(frontend_templates, exist_ok=True)

ocr_processor = OCRProcessor()

def get_db_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"Database connection error: {e}")
        return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required', 'redirect': '/login'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def log_invoice_action(invoice_id, action, user_id, username, user_email, details=None, changes=None, ip_address=None, user_agent=None):
    connection = get_db_connection()
    if not connection:
        return
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO invoice_logs (invoice_id, action, user_id, username, user_email, action_details, changes, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (invoice_id, action, user_id, username, user_email, details, json.dumps(changes) if changes else None, ip_address, user_agent))
        connection.commit()
    except Error as e:
        print(f"Error logging action: {e}")
    finally:
        cursor.close()
        connection.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def clean_amount(value):
    """Clean and convert monetary values to float"""
    if value is None or value == '':
        return None
    try:

        cleaned = re.sub(r'[^\d.-]', '', str(value))
        if cleaned and cleaned != '-' and cleaned != '.':
            return float(cleaned)
        return None
    except (ValueError, TypeError):
        return None

def send_reset_email(to_email, code):

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Your ConstruInvoice password reset code'
        msg['From']    = f'ConstruInvoice <{SMTP_USER}>'
        msg['To']      = to_email

        text = f"""
Hi,

Your ConstruInvoice password reset code is:

  {code}

This code expires in 10 minutes. If you did not request a password reset, you can safely ignore this email.

— ConstruInvoice Team
"""

        html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#F9FAFB;font-family:'DM Sans',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;padding:40px 16px;">
    <tr><td align="center">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
        <tr><td align="center" style="padding-bottom:24px;">
          <table cellpadding="0" cellspacing="0"><tr>
            <td style="background:#2563EB;border-radius:12px;padding:10px 14px;">
              <span style="color:white;font-size:18px;font-weight:600;letter-spacing:-0.3px;">Constru<span style="opacity:0.75;">Invoice</span></span>
              </td>
            </tr>
          </table>
        </table>
        <tr><td style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:40px;">
          <p style="font-size:22px;font-weight:600;color:#111827;margin:0 0 8px;letter-spacing:-0.3px;">Password reset code</p>
          <p style="font-size:14px;color:#6B7280;margin:0 0 32px;line-height:1.5;">
            We received a request to reset your ConstruInvoice password. Use the code below — it expires in <strong style="color:#374151;">10 minutes</strong>.
          </p>
          <div style="background:#EFF6FF;border:1.5px solid #BFDBFE;border-radius:12px;padding:24px;text-align:center;margin-bottom:32px;">
            <p style="font-size:38px;font-weight:700;letter-spacing:12px;color:#1D4ED8;margin:0;font-family:monospace;">{code}</p>
          </div>
          <p style="font-size:13px;color:#9CA3AF;margin:0;line-height:1.6;">
            If you didn't request this, you can safely ignore this email — your password will not change.<br><br>
            For security, never share this code with anyone.
          </p>
         </td>
        <tr><td align="center" style="padding-top:24px;">
          <p style="font-size:12px;color:#9CA3AF;margin:0;">
            &copy; {datetime.now().year} ConstruInvoice &middot; info@invoicestruct.com
          </p>
         </tr>
       </table>
      </td>
    </tr>
   </table>
</body>
</html>
"""
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False

def send_invoice_email(to_email, invoice_data, payment_link):
    """Send invoice email to customer with Mark as Paid link"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'Invoice {invoice_data.get("invoice_number", "N/A")} from {invoice_data.get("vendor_name", "ConstruInvoice")}'
        msg['From'] = f'ConstruInvoice <{SMTP_USER}>'
        msg['To'] = to_email

        due_date_str = invoice_data.get('due_date', 'upon receipt')
        if isinstance(due_date_str, datetime):
            due_date_str = due_date_str.strftime('%B %d, %Y')
        
        total_amount = f"${float(invoice_data.get('total', 0)):,.2f}" if invoice_data.get('total') else '$0.00'
        

        items_html = ''
        items = invoice_data.get('items', [])
        if items:
            items_html = '<table style="width:100%; border-collapse: collapse; margin: 16px 0;">'
            items_html += '<tr style="background: #f3f4f6;"><th style="padding: 10px; text-align: left;">Description</th><th style="padding: 10px; text-align: right;">Qty</th><th style="padding: 10px; text-align: right;">Unit Price</th><th style="padding: 10px; text-align: right;">Total</th></tr>'
            for item in items:
                items_html += f'''
                    <tr style="border-bottom: 1px solid #e5e7eb;">
                        <td style="padding: 10px;">{item.get('description', '')}</td>
                        <td style="padding: 10px; text-align: right;">{item.get('quantity', '')}</td>
                        <td style="padding: 10px; text-align: right;">${float(item.get('unit_price', 0)):,.2f}</td>
                        <td style="padding: 10px; text-align: right;">${float(item.get('total', 0)):,.2f}</td>
                    </tr>
                '''
            items_html += '</table>'

        text = f"""
Dear Customer,

Please find your invoice details below:

Invoice: {invoice_data.get('invoice_number', 'N/A')}
Amount Due: {total_amount}
Due Date: {due_date_str}

Items:
{chr(10).join([f"- {item.get('description', '')}: {item.get('quantity', '')} x ${float(item.get('unit_price', 0)):,.2f} = ${float(item.get('total', 0)):,.2f}" for item in items]) if items else 'No items'}

To mark this invoice as paid, please click the link below:
{payment_link}

If you have any questions, please contact us at {SMTP_USER}.

Thank you for your business.

— ConstruInvoice Team
"""

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#F9FAFB;font-family:'DM Sans',Helvetica,Arial,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;padding:40px 16px;">
        <tr><td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">
                <tr><td align="center" style="padding-bottom:24px;">
                    <div style="background:#2563EB;border-radius:12px;padding:10px 14px;display:inline-block;">
                        <span style="color:white;font-size:18px;font-weight:600;">Constru<span style="opacity:0.75;">Invoice</span></span>
                    </div>
                 </div>
                <tr><td style="background:white;border:1px solid #E5E7EB;border-radius:16px;padding:32px;">
                    <h2 style="margin:0 0 8px;color:#111827;font-size:24px;">Invoice #{invoice_data.get('invoice_number', 'N/A')}</h2>
                    <p style="color:#6B7280;margin-bottom:24px;">Thank you for your business</p>
                    
                    <div style="background:#F9FAFB;border-radius:12px;padding:20px;margin-bottom:24px;">
                        <table width="100%">
                            <tr><td style="padding:8px 0;"><strong>Amount Due:</strong></td><td style="padding:8px 0;text-align:right;font-size:28px;color:#2563EB;">{total_amount}</td></tr>
                            <tr><td style="padding:8px 0;"><strong>Due Date:</strong></td><td style="padding:8px 0;text-align:right;">{due_date_str}</td></tr>
                            <tr><td style="padding:8px 0;"><strong>Vendor:</strong></td><td style="padding:8px 0;text-align:right;">{invoice_data.get('vendor_name', 'N/A')}</td></tr>
                        </table>
                    </div>
                    
                    {items_html}
                    
                    <div style="text-align:center;margin:30px 0;">
                        <a href="{payment_link}" style="display:inline-block;background:#059669;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:600;">
                            ✓ Mark as Paid
                        </a>
                        <p style="font-size:12px;color:#6B7280;margin-top:12px;">Clicking this button confirms payment has been received</p>
                    </div>
                    
                    <p style="color:#6B7280;font-size:14px;margin-top:30px;border-top:1px solid #E5E7EB;padding-top:20px;">
                        Questions? Contact us at {SMTP_USER}
                    </p>
                 </div>
             </div>
         </div>
     </div>
</body>
</html>
"""
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
        
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False

def generate_payment_link(invoice_id):
    """Generate a unique payment confirmation link"""
    token = secrets.token_urlsafe(32)
    
    connection = get_db_connection()
    if connection:
        cursor = connection.cursor()
        try:
            expires_at = datetime.now() + timedelta(days=30)
            cursor.execute("""
                INSERT INTO payment_tokens (invoice_id, token, expires_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE token = %s, expires_at = %s, used = 0
            """, (invoice_id, token, expires_at, token, expires_at))
            connection.commit()
        except Error as e:
            print(f"Error storing payment token: {e}")
        finally:
            cursor.close()
            connection.close()
    
    base_url = request.host_url.rstrip('/')
    payment_link = f"{base_url}/confirm-payment/{token}"
    
    return payment_link

@app.route('/api/invoice/<invoice_id>/send-to-customer', methods=['POST'])
@login_required
def send_invoice_to_customer(invoice_id):
    """Send invoice email to customer with payment confirmation link"""
    try:
        data = request.json
        customer_email = data.get('customer_email', '').strip()
        
        if not customer_email:
            return jsonify({'error': 'Customer email is required'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        

        cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        

        if session.get('role') != 'admin' and invoice['uploaded_by'] != session['user_id']:
            return jsonify({'error': 'Access denied'}), 403
        

        cursor.execute("SELECT * FROM invoice_items WHERE invoice_id = %s ORDER BY item_number", (invoice_id,))
        items = cursor.fetchall()
        

        invoice_data = {
            'invoice_number': invoice.get('invoice_number'),
            'total': invoice.get('total'),
            'due_date': invoice.get('due_date'),
            'vendor_name': invoice.get('vendor_name'),
            'customer_name': invoice.get('customer_name'),
            'items': items
        }
        

        payment_link = generate_payment_link(invoice_id)
        

        email_sent = send_invoice_email(customer_email, invoice_data, payment_link)
        
        if email_sent:
            cursor.execute("""
                UPDATE invoices 
                SET status = 'PENDING', 
                    email_sent_at = NOW(), 
                    email_sent_to = %s,
                    payment_link = %s,
                    payment_link_expires = DATE_ADD(NOW(), INTERVAL 30 DAY)
                WHERE id = %s
            """, (customer_email, payment_link, invoice_id))
            
            cursor.execute("""
                INSERT INTO payment_notifications (invoice_id, notification_type, sent_to, status)
                VALUES (%s, %s, %s, %s)
            """, (invoice_id, 'SENT_TO_CUSTOMER', customer_email, 'SENT'))
            
            connection.commit()
            
            log_invoice_action(
                invoice_id, 'SEND_TO_CUSTOMER', session['user_id'], session['username'], session['email'],
                f"Sent invoice to {customer_email}",
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )
            
            return jsonify({
                'success': True,
                'message': 'Invoice sent successfully to customer',
                'payment_link': payment_link
            }), 200
        else:
            return jsonify({'error': 'Failed to send email - please try again'}), 500
            
    except Exception as e:
        print(f"Error sending invoice: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if connection:
            cursor.close()
            connection.close()

@app.route('/confirm-payment/<token>', methods=['GET'])
def confirm_payment_page(token):
    """Public page for customers to confirm payment"""
    try:
        connection = get_db_connection()
        if not connection:
            return "Payment system temporarily unavailable", 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT i.*, pt.expires_at, pt.used
            FROM invoices i
            JOIN payment_tokens pt ON i.id = pt.invoice_id
            WHERE pt.token = %s
        """, (token,))
        
        invoice = cursor.fetchone()
        cursor.close()
        connection.close()
        
        if not invoice:
            return render_template('payment_expired.html'), 404
        

        if invoice['status'] == 'PAID':
            return render_template('payment_already_paid.html'), 400
        

        is_expired = invoice['expires_at'] < datetime.now()
        
        if is_expired:
            return render_template('payment_expired.html'), 404
        

        if invoice['used'] == 1:
            return render_template('payment_expired.html'), 404
        

        is_overdue = invoice['due_date'] and invoice['due_date'] < datetime.now().date() if invoice['due_date'] else False
        
        return render_template('confirm_payment.html', invoice=invoice, token=token, is_overdue=is_overdue)
        
    except Exception as e:
        print(f"Payment confirmation page error: {e}")
        return "Payment confirmation error", 500

@app.route('/api/confirm-payment/<token>', methods=['POST'])
def confirm_payment(token):
    """API endpoint to confirm payment and update invoice status"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT i.*, pt.id as token_id, pt.expires_at, pt.used
            FROM invoices i
            JOIN payment_tokens pt ON i.id = pt.invoice_id
            WHERE pt.token = %s
        """, (token,))
        
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Invalid payment link'}), 404
        
        if invoice['status'] == 'PAID':
            return jsonify({'error': 'This invoice has already been paid'}), 400
        

        if invoice['expires_at'] < datetime.now():
            return jsonify({'error': 'Payment link has expired'}), 400
        
        if invoice['used'] == 1:
            return jsonify({'error': 'This payment link has already been used'}), 400
        

        cursor.execute("""
            UPDATE invoices 
            SET status = 'PAID', 
                payment_received_date = NOW(),
                payment_method = 'customer_confirmation'
            WHERE id = %s
        """, (invoice['id'],))
        

        cursor.execute("UPDATE payment_tokens SET used = 1 WHERE id = %s", (invoice['token_id'],))
        

        cursor.execute("""
            INSERT INTO payment_status_log (invoice_id, old_status, new_status, reason)
            VALUES (%s, %s, %s, %s)
        """, (invoice['id'], invoice['status'], 'PAID', 'Customer confirmed payment'))
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'message': 'Payment confirmed successfully',
            'invoice_number': invoice['invoice_number']
        }), 200
        
    except Exception as e:
        print(f"Payment confirmation error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/index')
def index_alt():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard_page():
    return render_template('dashboard.html')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard_page'))
    return render_template('login.html')

@app.route('/signup')
def signup_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard_page'))
    return render_template('signup.html')

@app.route('/forgot-password')
def forgot_password_page():
    if 'user_id' in session:
        return redirect(url_for('dashboard_page'))
    return render_template('forgot_password.html')

@app.route('/logout')
def logout_page():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/pay/<token>')
def payment_page(token):
    """Public payment page for customers"""
    try:
        connection = get_db_connection()
        if not connection:
            return "Payment system temporarily unavailable", 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT i.*, pt.expires_at 
            FROM invoices i
            JOIN payment_tokens pt ON i.id = pt.invoice_id
            WHERE pt.token = %s AND pt.expires_at > NOW() AND pt.used = 0
        """, (token,))
        
        invoice = cursor.fetchone()
        cursor.close()
        connection.close()
        
        if not invoice:
            return render_template('payment_expired.html'), 404
        
        return render_template('payment_page.html', invoice=invoice, token=token)
        
    except Exception as e:
        print(f"Payment page error: {e}")
        return "Payment page error", 500


@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('username', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        full_name = data.get('full_name', '').strip()
        
        if not username or not email or not password:
            return jsonify({'error': 'All fields are required'}), 400
        if len(username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters'}), 400
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return jsonify({'error': 'Username can only contain letters, numbers, and underscore'}), 400
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            return jsonify({'error': 'Invalid email format'}), 400
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        password_hash = generate_password_hash(password)
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            cursor.close()
            connection.close()
            return jsonify({'error': 'That username is already taken'}), 400
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            cursor.close()
            connection.close()
            return jsonify({'error': 'An account with that email already exists'}), 400
        cursor.execute("""
            INSERT INTO users (username, email, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s, %s)
        """, (username, email, password_hash, full_name, 'user'))
        connection.commit()
        user_id = cursor.lastrowid
        

        cursor.execute("""
            INSERT INTO user_settings (user_id, default_currency, email_notifications, payment_reminders)
            VALUES (%s, 'USD', TRUE, TRUE)
        """, (user_id,))
        connection.commit()
        
        cursor.close()
        connection.close()
        return jsonify({
            'success': True,
            'message': 'Registration successful! Please login.',
            'user': {'id': user_id, 'username': username, 'email': email}
        }), 201
    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        username_or_email = data.get('username', '').strip()
        password = data.get('password', '')
        
        if not username_or_email or not password:
            return jsonify({'error': 'Username/email and password are required'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, username, email, password_hash, full_name, role, is_active
            FROM users
            WHERE username = %s OR email = %s
        """, (username_or_email, username_or_email))
        user = cursor.fetchone()
        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Incorrect username or password'}), 401
        if not user['is_active']:
            return jsonify({'error': 'This account has been disabled — please contact support'}), 403
        cursor.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user['id'],))
        connection.commit()
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['email'] = user['email']
        session['full_name'] = user['full_name']
        session['role'] = user['role']
        cursor.close()
        connection.close()
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'redirect': '/dashboard',
            'user': {
                'id': user['id'],
                'username': user['username'],
                'email': user['email'],
                'full_name': user['full_name'],
                'role': user['role']
            }
        }), 200
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({
            'authenticated': True,
            'user': {
                'id': session['user_id'],
                'username': session['username'],
                'email': session['email'],
                'full_name': session.get('full_name'),
                'role': session.get('role')
            }
        }), 200
    return jsonify({'authenticated': False}), 200

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'}), 200


@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():

    try:
        data  = request.json
        email = data.get('email', '').strip().lower()
        if not email or not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
            return jsonify({'error': 'Please enter a valid email address'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s AND is_active = 1", (email,))
        user = cursor.fetchone()
        if user:
            code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            expires_at = datetime.now() + timedelta(minutes=10)
            cursor.execute("DELETE FROM password_reset_codes WHERE email = %s", (email,))
            cursor.execute(
                "INSERT INTO password_reset_codes (email, code, expires_at) VALUES (%s, %s, %s)",
                (email, code, expires_at)
            )
            connection.commit()
            sent = send_reset_email(email, code)
            if not sent:
                cursor.close()
                connection.close()
                return jsonify({'error': 'Failed to send email — please try again shortly'}), 500
        cursor.close()
        connection.close()
        return jsonify({
            'success': True,
            'message': 'If an account exists for that email, a reset code has been sent.'
        }), 200
    except Exception as e:
        print(f"Forgot password error: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500

@app.route('/api/verify-reset-code', methods=['POST'])
def verify_reset_code():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()
        if not email or not code:
            return jsonify({'error': 'Email and code are required'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT id FROM password_reset_codes
               WHERE email = %s AND code = %s AND expires_at > NOW() AND used = 0""",
            (email, code)
        )
        record = cursor.fetchone()
        cursor.close()
        connection.close()
        if not record:
            return jsonify({'error': 'Incorrect or expired code — please try again'}), 400
        return jsonify({'success': True}), 200
    except Exception as e:
        print(f"Verify code error: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        new_password = data.get('new_password', '')
        if not email or not new_password:
            return jsonify({'error': 'Email and new password are required'}), 400
        if len(new_password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT id FROM password_reset_codes
               WHERE email = %s AND expires_at > NOW() AND used = 0
               ORDER BY created_at DESC LIMIT 1""",
            (email,)
        )
        record = cursor.fetchone()
        if not record:
            cursor.close()
            connection.close()
            return jsonify({'error': 'Reset session expired — please start over'}), 400
        new_hash = generate_password_hash(new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE email = %s", (new_hash, email))
        cursor.execute("UPDATE password_reset_codes SET used = 1 WHERE id = %s", (record['id'],))
        connection.commit()
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'message': 'Password reset successfully'}), 200
    except Exception as e:
        print(f"Reset password error: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500


@app.route('/api/upload-invoice', methods=['POST'])
@login_required
def upload_invoice():
    """
    Upload and process an invoice file using OCR.
    Handles duplicate detection, file validation, and database insertion.
    """
    file = None
    filepath = None
    connection = None
    cursor = None
    
    try:

        if 'file' not in request.files:
            return jsonify({'error': 'No file provided. Please select a file to upload.'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected. Please choose a file to upload.'}), 400
        
        if not allowed_file(file.filename):
            allowed_extensions = ', '.join(ALLOWED_EXTENSIONS).upper()
            return jsonify({'error': f'File type not allowed. Supported formats: {allowed_extensions}'}), 400
        

        original_filename = file.filename
        filename = secure_filename(f"{uuid.uuid4()}_{original_filename}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        

        if not os.path.exists(filepath):
            return jsonify({'error': 'Failed to save uploaded file. Please try again.'}), 500
        
        file_size = os.path.getsize(filepath)
        

        try:
            invoice_data = ocr_processor.process_invoice(filepath)
        except Exception as ocr_error:
            print(f"OCR processing error: {ocr_error}")

            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': 'Failed to process invoice. The file may be corrupted or unreadable.'}), 500
        

        invoice_number = invoice_data.get('invoice_number')
        if invoice_number and str(invoice_number).strip():
            try:
                dup_connection = get_db_connection()
                if dup_connection:
                    dup_cursor = dup_connection.cursor()

                    dup_cursor.execute(
                        "SELECT id, invoice_number, upload_date FROM invoices WHERE invoice_number = %s AND uploaded_by = %s",
                        (invoice_number, session['user_id'])
                    )
                    existing = dup_cursor.fetchone()
                    dup_cursor.close()
                    dup_connection.close()
                    
                    if existing:

                        if os.path.exists(filepath):
                            os.remove(filepath)
                        
                        return jsonify({
                            'error': f'You have already uploaded Invoice #{invoice_number}. Please use a different invoice number.',
                            'duplicate': True,
                            'existing_invoice_id': existing[0] if existing else None
                        }), 409
            except Exception as dup_error:
                print(f"Duplicate check error: {dup_error}")
        


        if not invoice_data.get('invoice_number'):
            invoice_data['invoice_number'] = f"TEMP-{uuid.uuid4().hex[:8].upper()}"
        

        def clean_amount(value):
            if value is None or value == '':
                return None
            try:

                cleaned = re.sub(r'[^\d.-]', '', str(value))
                if cleaned and cleaned != '-':
                    return float(cleaned)
                return None
            except (ValueError, TypeError):
                return None
        
        invoice_data['subtotal'] = clean_amount(invoice_data.get('subtotal'))
        invoice_data['tax'] = clean_amount(invoice_data.get('tax'))
        invoice_data['total'] = clean_amount(invoice_data.get('total'))
        

        invoice_id = str(uuid.uuid4())
        connection = get_db_connection()
        
        if not connection:

            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({'error': 'Database connection failed. Please try again later.'}), 500
        
        cursor = connection.cursor()
        

        cursor.execute("""
            INSERT INTO invoices (
                id, invoice_number, date, due_date, vendor_name, vendor_address, 
                customer_name, customer_address, subtotal, tax, total, currency,
                payment_terms, notes, file_path, file_name, file_size, status,
                upload_date, uploaded_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT', NOW(), %s)
        """, (
            invoice_id,
            invoice_data.get('invoice_number'),
            invoice_data.get('date'),
            invoice_data.get('due_date'),
            invoice_data.get('vendor_name'),
            invoice_data.get('vendor_address'),
            invoice_data.get('customer_name'),
            invoice_data.get('customer_address'),
            invoice_data.get('subtotal'),
            invoice_data.get('tax'),
            invoice_data.get('total'),
            invoice_data.get('currency', 'USD'),
            invoice_data.get('payment_terms'),
            invoice_data.get('notes'),
            filepath,
            original_filename,
            file_size,
            session['user_id']
        ))
        

        const_data = invoice_data.get('construction_specific', {})
        if const_data and any(const_data.values()):
            cursor.execute("""
                INSERT INTO construction_details (
                    invoice_id, project_name, project_address, project_number,
                    contractor_license, work_order, material_cost, labor_cost,
                    equipment_cost, permit_number
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                invoice_id,
                const_data.get('project_name'),
                const_data.get('project_address'),
                const_data.get('project_number'),
                const_data.get('contractor_license'),
                const_data.get('work_order'),
                const_data.get('material_cost'),
                const_data.get('labor_cost'),
                const_data.get('equipment_cost'),
                const_data.get('permit_number')
            ))
        

        items = invoice_data.get('items', [])
        if items:
            for idx, item in enumerate(items, 1):

                item_description = item.get('description', '').strip()
                if not item_description:
                    continue
                
                cursor.execute("""
                    INSERT INTO invoice_items (
                        invoice_id, item_number, description, quantity, unit_price, total
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    invoice_id, idx,
                    item_description[:500],
                    str(item.get('quantity', ''))[:50],
                    clean_amount(item.get('unit_price')),
                    clean_amount(item.get('total'))
                ))
        
        connection.commit()
        

        log_invoice_action(
            invoice_id, 'UPLOAD', session['user_id'], session['username'], session['email'],
            f"Uploaded invoice file: {original_filename} (Invoice #{invoice_data.get('invoice_number', 'N/A')})",
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        

        invoice_data['id'] = invoice_id
        
        return jsonify({
            'success': True,
            'message': f'Invoice #{invoice_data.get("invoice_number", "N/A")} processed and saved successfully!',
            'invoice_id': invoice_id,
            'data': invoice_data
        }), 200
        
    except mysql.connector.Error as db_error:
        print(f"Database error during upload: {db_error}")
        return jsonify({'error': 'Database error occurred. Please try again.'}), 500
        
    except Exception as e:
        print(f"Unexpected error during upload: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500
        
    finally:

        if cursor:
            try:
                cursor.close()
            except:
                pass
        if connection:
            try:
                connection.close()
            except:
                pass



@app.route('/api/invoices', methods=['GET'])
@login_required
def get_all_invoices():
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        current_user_id = session['user_id']
        user_role = session.get('role', 'user')
        
        if user_role == 'admin':
            cursor.execute("""
                SELECT i.*, u.username as uploaded_by_name
                FROM invoices i
                LEFT JOIN users u ON i.uploaded_by = u.id
                ORDER BY i.upload_date DESC
            """)
        else:
            cursor.execute("""
                SELECT i.*, u.username as uploaded_by_name
                FROM invoices i
                LEFT JOIN users u ON i.uploaded_by = u.id
                WHERE i.uploaded_by = %s
                ORDER BY i.upload_date DESC
            """, (current_user_id,))
        
        invoices = cursor.fetchall()
        
        for invoice in invoices:
            if invoice.get('upload_date'):
                invoice['upload_date'] = invoice['upload_date'].isoformat() if hasattr(invoice['upload_date'], 'isoformat') else str(invoice['upload_date'])
            if invoice.get('date'):
                invoice['date'] = invoice['date'].isoformat() if hasattr(invoice['date'], 'isoformat') else str(invoice['date'])
            if invoice.get('due_date'):
                invoice['due_date'] = invoice['due_date'].isoformat() if hasattr(invoice['due_date'], 'isoformat') else str(invoice['due_date'])
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'invoices': invoices,
            'user_id': current_user_id,
            'role': user_role
        }), 200
        
    except Exception as e:
        print(f"Error fetching invoices: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Something went wrong — please try again'}), 500

@app.route('/api/invoice/<invoice_id>', methods=['GET'])
@login_required
def get_invoice(invoice_id):
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT i.*, u.username as uploaded_by_name
            FROM invoices i
            LEFT JOIN users u ON i.uploaded_by = u.id
            WHERE i.id = %s
        """, (invoice_id,))
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        
        if session.get('role') != 'admin' and invoice['uploaded_by'] != session['user_id']:
            return jsonify({'error': 'Access denied'}), 403
        
        cursor.execute("SELECT * FROM construction_details WHERE invoice_id = %s", (invoice_id,))
        const_details = cursor.fetchone()
        
        cursor.execute("SELECT * FROM invoice_items WHERE invoice_id = %s ORDER BY item_number", (invoice_id,))
        items = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        log_invoice_action(
            invoice_id, 'VIEW', session['user_id'], session['username'], session['email'],
            f"Viewed invoice {invoice.get('invoice_number', invoice_id)}",
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        
        combined_data = {
            'id': invoice.get('id'),
            'invoice_number': invoice.get('invoice_number'),
            'date': invoice.get('date').isoformat() if invoice.get('date') else None,
            'due_date': invoice.get('due_date').isoformat() if invoice.get('due_date') else None,
            'vendor_name': invoice.get('vendor_name'),
            'vendor_address': invoice.get('vendor_address'),
            'customer_name': invoice.get('customer_name'),
            'customer_address': invoice.get('customer_address'),
            'subtotal': float(invoice['subtotal']) if invoice.get('subtotal') else None,
            'tax': float(invoice['tax']) if invoice.get('tax') else None,
            'total': float(invoice['total']) if invoice.get('total') else None,
            'currency': invoice.get('currency', 'USD'),
            'payment_terms': invoice.get('payment_terms'),
            'notes': invoice.get('notes'),
            'status': invoice.get('status', 'DRAFT'),
            'raw_text': None,
            'ocr_engine': 'Smart Processing',
            'construction_specific': {
                'project_name': const_details.get('project_name') if const_details else None,
                'project_address': const_details.get('project_address') if const_details else None,
                'project_number': const_details.get('project_number') if const_details else None,
                'contractor_license': const_details.get('contractor_license') if const_details else None,
                'work_order': const_details.get('work_order') if const_details else None,
                'material_cost': float(const_details['material_cost']) if const_details and const_details.get('material_cost') else None,
                'labor_cost': float(const_details['labor_cost']) if const_details and const_details.get('labor_cost') else None,
                'equipment_cost': float(const_details['equipment_cost']) if const_details and const_details.get('equipment_cost') else None,
                'permit_number': const_details.get('permit_number') if const_details else None,
            },
            'items': [
                {
                    'description': item.get('description'),
                    'quantity': item.get('quantity'),
                    'unit_price': float(item['unit_price']) if item.get('unit_price') else None,
                    'total': float(item['total']) if item.get('total') else None
                }
                for item in items
            ]
        }
        
        return jsonify({
            'success': True,
            'data': combined_data        }), 200
        
    except Exception as e:
        print(f"Error fetching invoice: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/invoice/<invoice_id>', methods=['PUT'])
@login_required
def update_invoice(invoice_id):
    try:
        data = request.json
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        old_invoice = cursor.fetchone()
        if not old_invoice:
            cursor.close()
            connection.close()
            return jsonify({'error': 'Invoice not found'}), 404
        if session.get('role') != 'admin' and old_invoice['uploaded_by'] != session['user_id']:
            cursor.close()
            connection.close()
            return jsonify({'error': 'Access denied'}), 403


        new_invoice_number = data.get('invoice_number')
        if new_invoice_number and new_invoice_number != old_invoice['invoice_number']:
            cursor.execute(
                "SELECT id FROM invoices WHERE invoice_number = %s AND uploaded_by = %s AND id != %s",
                (new_invoice_number, session['user_id'], invoice_id)
            )
            if cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({'error': f'Invoice number {new_invoice_number} already exists'}), 409

        changes = {}
        fields_to_track = ['invoice_number', 'vendor_name', 'customer_name', 'subtotal', 'tax', 'total', 'due_date']
        for field in fields_to_track:
            old_value = old_invoice.get(field)
            new_value = data.get(field)
            if str(old_value) != str(new_value):
                changes[field] = {'old': str(old_value) if old_value else None, 'new': str(new_value) if new_value else None}

        cursor.execute("""
            UPDATE invoices 
            SET invoice_number = %s, date = %s, due_date = %s, vendor_name = %s,
                vendor_address = %s, customer_name = %s, customer_address = %s,
                subtotal = %s, tax = %s, total = %s, payment_terms = %s, notes = %s,
                last_modified = NOW(), modified_by = %s
            WHERE id = %s
        """, (
            data.get('invoice_number'),
            data.get('date'),
            data.get('due_date'),
            data.get('vendor_name'),
            data.get('vendor_address'),
            data.get('customer_name'),
            data.get('customer_address'),
            data.get('subtotal'),
            data.get('tax'),
            data.get('total'),
            data.get('payment_terms'),
            data.get('notes'),
            session['user_id'],
            invoice_id
        ))

        if data.get('construction_specific'):
            const = data['construction_specific']
            cursor.execute("""
                UPDATE construction_details 
                SET project_name = %s, project_address = %s, project_number = %s,
                    contractor_license = %s, work_order = %s, material_cost = %s,
                    labor_cost = %s, equipment_cost = %s, permit_number = %s
                WHERE invoice_id = %s
            """, (
                const.get('project_name'),
                const.get('project_address'),
                const.get('project_number'),
                const.get('contractor_license'),
                const.get('work_order'),
                const.get('material_cost'),
                const.get('labor_cost'),
                const.get('equipment_cost'),
                const.get('permit_number'),
                invoice_id
            ))


        cursor.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
        items = data.get('items', [])
        for idx, item in enumerate(items, 1):
            item_description = (item.get('description') or '').strip()
            if not item_description:
                continue
            cursor.execute("""
                INSERT INTO invoice_items (invoice_id, item_number, description, quantity, unit_price, total)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                invoice_id, idx,
                item_description[:500],
                str(item.get('quantity', ''))[:50],
                clean_amount(item.get('unit_price')),
                clean_amount(item.get('total'))
            ))

        connection.commit()

        if changes:
            log_invoice_action(
                invoice_id, 'EDIT', session['user_id'], session['username'], session['email'],
                f"Edited invoice {old_invoice.get('invoice_number', invoice_id)}",
                changes=changes,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )

        cursor.close()
        connection.close()

        return jsonify({
            'success': True,
            'message': 'Invoice updated successfully',
            'changes': changes
        }), 200

    except Exception as e:
        print(f"Error updating invoice: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Something went wrong — please try again'}), 500
    
@app.route('/api/invoice/<invoice_id>', methods=['DELETE'])
@login_required
def delete_invoice(invoice_id):
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT invoice_number, file_path FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cursor.fetchone()
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        
        cursor.execute("SELECT uploaded_by FROM invoices WHERE id = %s", (invoice_id,))
        owner = cursor.fetchone()
        if session.get('role') != 'admin' and owner['uploaded_by'] != session['user_id']:
            return jsonify({'error': 'Access denied'}), 403
        
        log_invoice_action(
            invoice_id, 'DELETE', session['user_id'], session['username'], session['email'],
            f"Deleted invoice {invoice.get('invoice_number', invoice_id)}",
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        
        if invoice.get('file_path') and os.path.exists(invoice['file_path']):
            os.remove(invoice['file_path'])
        
        cursor.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
        connection.commit()
        cursor.close()
        connection.close()
        return jsonify({'success': True, 'message': 'Invoice deleted successfully'}), 200
    except Exception as e:
        print(f"Error deleting invoice: {e}")
        return jsonify({'error': 'Something went wrong — please try again'}), 500




@app.route('/api/invoice/<invoice_id>/status', methods=['PUT'])
@login_required
def update_invoice_status(invoice_id):
    """Update invoice status (PAID, CANCELLED, etc.)"""
    try:
        data = request.json
        new_status = data.get('status')
        payment_method = data.get('payment_method')
        transaction_id = data.get('transaction_id')
        reason = data.get('reason')
        
        valid_statuses = ['PENDING', 'PAID', 'OVERDUE', 'CANCELLED']
        if new_status not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}), 400
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        
        if session.get('role') != 'admin' and invoice['uploaded_by'] != session['user_id']:
            return jsonify({'error': 'Access denied'}), 403
        
        old_status = invoice.get('status', 'DRAFT')
        

        update_fields = ["status = %s"]
        params = [new_status]
        
        if new_status == 'PAID':
            update_fields.append("payment_received_date = NOW()")
            if payment_method:
                update_fields.append("payment_method = %s")
                params.append(payment_method)
            if transaction_id:
                update_fields.append("payment_transaction_id = %s")
                params.append(transaction_id)
            
            cursor.execute("""
                INSERT INTO payment_notifications (invoice_id, notification_type, sent_to, status)
                VALUES (%s, %s, %s, %s)
            """, (invoice_id, 'PAYMENT_CONFIRMATION', session['email'], 'SENT'))
        
        params.append(invoice_id)
        
        query = f"UPDATE invoices SET {', '.join(update_fields)} WHERE id = %s"
        cursor.execute(query, tuple(params))
        

        cursor.execute("""
            INSERT INTO payment_status_log (invoice_id, old_status, new_status, changed_by, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (invoice_id, old_status, new_status, session['user_id'], reason))
        
        connection.commit()
        

        action_type = f'STATUS_{new_status}'
        log_invoice_action(
            invoice_id, action_type, session['user_id'], session['username'], session['email'],
            f"Changed invoice status from {old_status} to {new_status}" + (f" - Reason: {reason}" if reason else ""),
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        
        return jsonify({
            'success': True,
            'message': f'Invoice status updated to {new_status}',
            'old_status': old_status,
            'new_status': new_status
        }), 200
        
    except Exception as e:
        print(f"Error updating invoice status: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if connection:
            cursor.close()
            connection.close()

@app.route('/api/invoice/<invoice_id>/payment-status', methods=['GET'])
@login_required
def get_payment_status(invoice_id):
    """Get detailed payment status for an invoice"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, status, payment_link, payment_link_expires, 
                   payment_received_date, payment_method, payment_transaction_id,
                   email_sent_at, email_sent_to, due_date
            FROM invoices 
            WHERE id = %s
        """, (invoice_id,))
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        
        cursor.execute("""
            SELECT * FROM payment_status_log 
            WHERE invoice_id = %s 
            ORDER BY created_at DESC
        """, (invoice_id,))
        status_history = cursor.fetchall()
        
        cursor.execute("""
            SELECT * FROM payment_notifications 
            WHERE invoice_id = %s 
            ORDER BY sent_at DESC
        """, (invoice_id,))
        notifications = cursor.fetchall()
        

        is_overdue = False
        if invoice.get('due_date') and invoice.get('status') not in ['PAID', 'CANCELLED']:
            if isinstance(invoice['due_date'], datetime):
                due_date = invoice['due_date']
            else:
                due_date = invoice['due_date']
            if due_date < datetime.now().date():
                is_overdue = True
                if invoice.get('status') != 'OVERDUE':
                    cursor.execute("""
                        UPDATE invoices SET status = 'OVERDUE' WHERE id = %s
                    """, (invoice_id,))
                    connection.commit()
                    invoice['status'] = 'OVERDUE'
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'payment_status': {
                'status': invoice.get('status'),
                'is_overdue': is_overdue,
                'payment_link': invoice.get('payment_link'),
                'payment_link_expires': invoice.get('payment_link_expires'),
                'payment_received_date': invoice.get('payment_received_date'),
                'payment_method': invoice.get('payment_method'),
                'transaction_id': invoice.get('payment_transaction_id'),
                'email_sent_to': invoice.get('email_sent_to'),
                'email_sent_at': invoice.get('email_sent_at')
            },
            'status_history': status_history,
            'notifications': notifications
        }), 200
        
    except Exception as e:
        print(f"Error getting payment status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/check-overdue-invoices', methods=['POST'])
@login_required
@admin_required
def check_overdue_invoices():
    """Check and update overdue invoices"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, invoice_number, due_date, email_sent_to 
            FROM invoices 
            WHERE status = 'PENDING' 
            AND due_date IS NOT NULL 
            AND due_date < CURDATE()
        """)
        overdue_invoices = cursor.fetchall()
        
        updated_count = 0
        for invoice in overdue_invoices:
            cursor.execute("""
                UPDATE invoices SET status = 'OVERDUE' WHERE id = %s
            """, (invoice['id'],))
            
            cursor.execute("""
                INSERT INTO payment_status_log (invoice_id, old_status, new_status, reason)
                VALUES (%s, %s, %s, %s)
            """, (invoice['id'], 'PENDING', 'OVERDUE', 'Auto-updated: past due date'))
            
            updated_count += 1
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'message': f'Updated {updated_count} overdue invoices',
            'overdue_count': updated_count
        }), 200
        
    except Exception as e:
        print(f"Error checking overdue invoices: {e}")
        return jsonify({'error': str(e)}), 500



@app.route('/api/financial/balance-sheet', methods=['GET'])
@login_required
def get_balance_sheet():
    """Generate balance sheet report"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        user_id = session['user_id']
        user_role = session.get('role', 'user')
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END), 0) as total_paid,
                    COALESCE(SUM(CASE WHEN status = 'PENDING' THEN total ELSE 0 END), 0) as total_pending,
                    COALESCE(SUM(CASE WHEN status = 'OVERDUE' THEN total ELSE 0 END), 0) as total_overdue,
                    COALESCE(SUM(total), 0) as total_invoiced,
                    COUNT(*) as total_invoices,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid_invoices,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending_invoices,
                    COUNT(CASE WHEN status = 'OVERDUE' THEN 1 END) as overdue_invoices
                FROM invoices
            """)
        else:
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END), 0) as total_paid,
                    COALESCE(SUM(CASE WHEN status = 'PENDING' THEN total ELSE 0 END), 0) as total_pending,
                    COALESCE(SUM(CASE WHEN status = 'OVERDUE' THEN total ELSE 0 END), 0) as total_overdue,
                    COALESCE(SUM(total), 0) as total_invoiced,
                    COUNT(*) as total_invoices,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid_invoices,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending_invoices,
                    COUNT(CASE WHEN status = 'OVERDUE' THEN 1 END) as overdue_invoices
                FROM invoices
                WHERE uploaded_by = %s
            """, (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        connection.close()
        

        def safe_float(value):
            if value is None:
                return 0.0
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        
        total_pending = safe_float(result.get('total_pending'))
        total_overdue = safe_float(result.get('total_overdue'))
        total_paid = safe_float(result.get('total_paid'))
        
        total_receivable = total_pending + total_overdue
        total_assets = total_receivable + total_paid
        
        return jsonify({
            'success': True,
            'balance_sheet': {
                'assets': {
                    'accounts_receivable': round(total_receivable, 2),
                    'cash_and_bank': round(total_paid, 2),
                    'total_assets': round(total_assets, 2)
                },
                'liabilities': {
                    'accounts_payable': 0,
                    'total_liabilities': 0
                },
                'equity': {
                    'retained_earnings': round(total_paid, 2),
                    'total_equity': round(total_paid, 2)
                },
                'summary': {
                    'total_invoiced': round(safe_float(result.get('total_invoiced')), 2),
                    'total_paid': round(total_paid, 2),
                    'total_receivable': round(total_receivable, 2),
                    'total_overdue': round(total_overdue, 2),
                    'total_invoices': result.get('total_invoices', 0),
                    'paid_invoices': result.get('paid_invoices', 0),
                    'pending_invoices': result.get('pending_invoices', 0),
                    'overdue_invoices': result.get('overdue_invoices', 0)
                }
            }
        }), 200
        
    except Exception as e:
        print(f"Error generating balance sheet: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/financial/profit-loss', methods=['GET'])
@login_required
def get_profit_loss():
    """Generate Profit & Loss statement"""
    try:
        period = request.args.get('period', 'year')
        year = request.args.get('year', datetime.now().year)
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        user_id = session['user_id']
        user_role = session.get('role', 'user')
        


        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(total), 0) as total_revenue,
                    COALESCE(SUM(subtotal), 0) as total_subtotal,
                    COALESCE(SUM(tax), 0) as total_tax,
                    COUNT(*) as invoice_count,
                    COALESCE(AVG(total), 0) as avg_invoice_value
                FROM invoices
                WHERE status = 'PAID'
            """)
        else:
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(total), 0) as total_revenue,
                    COALESCE(SUM(subtotal), 0) as total_subtotal,
                    COALESCE(SUM(tax), 0) as total_tax,
                    COUNT(*) as invoice_count,
                    COALESCE(AVG(total), 0) as avg_invoice_value
                FROM invoices
                WHERE status = 'PAID' AND uploaded_by = %s
            """, (user_id,))
        
        result = cursor.fetchone()
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(upload_date, '%%Y-%%m') as month,
                    COALESCE(SUM(total), 0) as revenue,
                    COUNT(*) as invoice_count
                FROM invoices
                WHERE status = 'PAID'
                GROUP BY DATE_FORMAT(upload_date, '%%Y-%%m')
                ORDER BY month DESC
                LIMIT 12
            """)
        else:
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(upload_date, '%%Y-%%m') as month,
                    COALESCE(SUM(total), 0) as revenue,
                    COUNT(*) as invoice_count
                FROM invoices
                WHERE status = 'PAID' AND uploaded_by = %s
                GROUP BY DATE_FORMAT(upload_date, '%%Y-%%m')
                ORDER BY month DESC
                LIMIT 12
            """, (user_id,))
        
        monthly_breakdown = cursor.fetchall()
        cursor.close()
        connection.close()
        
        total_revenue = float(result.get('total_revenue', 0) or 0)
        total_subtotal = float(result.get('total_subtotal', 0) or 0)
        

        estimated_expenses = total_subtotal * 0.6
        gross_profit = total_revenue - estimated_expenses
        gross_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0
        
        return jsonify({
            'success': True,
            'profit_loss': {
                'revenue': {
                    'total': total_revenue,
                    'by_month': monthly_breakdown
                },
                'expenses': {
                    'estimated_cost_of_revenue': estimated_expenses,
                    'total_expenses': estimated_expenses
                },
                'profit': {
                    'gross_profit': gross_profit,
                    'gross_margin': round(gross_margin, 1),
                    'net_profit': gross_profit
                },
                'summary': {
                    'total_invoices': result.get('invoice_count', 0),
                    'avg_invoice_value': round(float(result.get('avg_invoice_value', 0) or 0), 2),
                    'total_tax_collected': round(float(result.get('total_tax', 0) or 0), 2),
                    'period': period,
                    'year': year
                }
            }
        }), 200
        
    except Exception as e:
        print(f"Error generating profit/loss: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/financial/cash-flow', methods=['GET'])
@login_required
def get_cash_flow():
    """Generate cash flow statement"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        user_id = session['user_id']
        user_role = session.get('role', 'user')
        
        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    DATE(payment_received_date) as date,
                    SUM(total) as inflow
                FROM invoices
                WHERE status = 'PAID' AND payment_received_date IS NOT NULL
                GROUP BY DATE(payment_received_date)
                ORDER BY date DESC
                LIMIT 90
            """)
        else:
            cursor.execute("""
                SELECT 
                    DATE(payment_received_date) as date,
                    SUM(total) as inflow
                FROM invoices
                WHERE status = 'PAID' AND uploaded_by = %s AND payment_received_date IS NOT NULL
                GROUP BY DATE(payment_received_date)
                ORDER BY date DESC
                LIMIT 90
            """, (user_id,))
        
        inflows = cursor.fetchall()
        
        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    DATE(due_date) as due_date,
                    SUM(total) as expected_inflow
                FROM invoices
                WHERE status = 'PENDING' AND due_date IS NOT NULL
                GROUP BY DATE(due_date)
                ORDER BY due_date ASC
                LIMIT 60
            """)
        else:
            cursor.execute("""
                SELECT 
                    DATE(due_date) as due_date,
                    SUM(total) as expected_inflow
                FROM invoices
                WHERE status = 'PENDING' AND uploaded_by = %s AND due_date IS NOT NULL
                GROUP BY DATE(due_date)
                ORDER BY due_date ASC
                LIMIT 60
            """, (user_id,))
        
        expected_inflows = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        total_inflow = sum(float(i['inflow'] or 0) for i in inflows)
        total_expected = sum(float(e['expected_inflow'] or 0) for e in expected_inflows)
        
        return jsonify({
            'success': True,
            'cash_flow': {
                'cash_inflows': {
                    'received': total_inflow,
                    'received_count': len(inflows),
                    'daily_breakdown': inflows
                },
                'expected_inflows': {
                    'pending': total_expected,
                    'expected_count': len(expected_inflows),
                    'upcoming_breakdown': expected_inflows
                },
                'summary': {
                    'total_cash_received': total_inflow,
                    'cash_to_be_received': total_expected,
                    'projected_cash': total_inflow + total_expected
                }
            }
        }), 200
        
    except Exception as e:
        print(f"Error generating cash flow: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/financial/invoice-analytics', methods=['GET'])
@login_required
def get_invoice_analytics():
    """Get detailed invoice analytics"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        user_id = session['user_id']
        user_role = session.get('role', 'user')
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending,
                    COUNT(CASE WHEN status = 'OVERDUE' THEN 1 END) as overdue
                FROM invoices
            """)
            status_counts = cursor.fetchone()
        else:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending,
                    COUNT(CASE WHEN status = 'OVERDUE' THEN 1 END) as overdue
                FROM invoices
                WHERE uploaded_by = %s
            """, (user_id,))
            status_counts = cursor.fetchone()
        

        aging_query = """
            SELECT 
                CASE 
                    WHEN due_date >= CURDATE() OR due_date IS NULL THEN 'Current'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 1 AND 30 THEN '1-30 Days'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 31 AND 60 THEN '31-60 Days'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 61 AND 90 THEN '61-90 Days'
                    ELSE '90+ Days'
                END as aging_bucket,
                COUNT(*) as invoice_count,
                COALESCE(SUM(total), 0) as total_amount
            FROM invoices
            WHERE status IN ('PENDING', 'OVERDUE')
        """
        
        if user_role != 'admin':
            aging_query += " AND uploaded_by = %s GROUP BY aging_bucket"
            cursor.execute(aging_query, (user_id,))
        else:
            aging_query += " GROUP BY aging_bucket"
            cursor.execute(aging_query)
        
        aging_data = cursor.fetchall()
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    customer_name,
                    COUNT(*) as invoice_count,
                    COALESCE(SUM(total), 0) as total_revenue,
                    COALESCE(AVG(total), 0) as avg_invoice
                FROM invoices
                WHERE status = 'PAID' AND customer_name IS NOT NULL AND customer_name != ''
                GROUP BY customer_name
                ORDER BY total_revenue DESC
                LIMIT 10
            """)
        else:
            cursor.execute("""
                SELECT 
                    customer_name,
                    COUNT(*) as invoice_count,
                    COALESCE(SUM(total), 0) as total_revenue,
                    COALESCE(AVG(total), 0) as avg_invoice
                FROM invoices
                WHERE status = 'PAID' AND uploaded_by = %s AND customer_name IS NOT NULL AND customer_name != ''
                GROUP BY customer_name
                ORDER BY total_revenue DESC
                LIMIT 10
            """, (user_id,))
        
        top_customers = cursor.fetchall()
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(upload_date, '%%Y-%%m') as month,
                    COUNT(*) as invoice_count,
                    COALESCE(SUM(total), 0) as total_amount,
                    COALESCE(AVG(total), 0) as avg_amount,
                    COALESCE(SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END), 0) as paid_amount
                FROM invoices
                GROUP BY DATE_FORMAT(upload_date, '%%Y-%%m')
                ORDER BY month DESC
                LIMIT 12
            """)
        else:
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(upload_date, '%%Y-%%m') as month,
                    COUNT(*) as invoice_count,
                    COALESCE(SUM(total), 0) as total_amount,
                    COALESCE(AVG(total), 0) as avg_amount,
                    COALESCE(SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END), 0) as paid_amount
                FROM invoices
                WHERE uploaded_by = %s
                GROUP BY DATE_FORMAT(upload_date, '%%Y-%%m')
                ORDER BY month DESC
                LIMIT 12
            """, (user_id,))
        
        monthly_trends = cursor.fetchall()
        


        paid_count = status_counts.get('paid', 0) if status_counts else 0
        
        if paid_count > 0:
            if user_role == 'admin':
                cursor.execute("""
                    SELECT 
                        COALESCE(ROUND(AVG(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 1), 0) as avg_days_to_pay,
                        COALESCE(MIN(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 0) as min_days_to_pay,
                        COALESCE(MAX(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 0) as max_days_to_pay
                    FROM invoices
                    WHERE status = 'PAID'
                """)
            else:
                cursor.execute("""
                    SELECT 
                        COALESCE(ROUND(AVG(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 1), 0) as avg_days_to_pay,
                        COALESCE(MIN(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 0) as min_days_to_pay,
                        COALESCE(MAX(DATEDIFF(COALESCE(payment_received_date, upload_date), upload_date)), 0) as max_days_to_pay
                    FROM invoices
                    WHERE status = 'PAID' AND uploaded_by = %s
                """, (user_id,))
            
            payment_efficiency = cursor.fetchone()
        else:

            payment_efficiency = {
                'avg_days_to_pay': 0,
                'min_days_to_pay': None,
                'max_days_to_pay': None
            }
        
        cursor.close()
        connection.close()
        

        avg_days = round(float(payment_efficiency.get('avg_days_to_pay', 0) or 0), 1)
        min_days = payment_efficiency.get('min_days_to_pay')
        max_days = payment_efficiency.get('max_days_to_pay')
        

        min_days_str = min_days if min_days is not None and min_days > 0 else None
        max_days_str = max_days if max_days is not None and max_days > 0 else None
        
        return jsonify({
            'success': True,
            'invoice_analytics': {
                'status_counts': {
                    'total': status_counts.get('total', 0) if status_counts else 0,
                    'paid': status_counts.get('paid', 0) if status_counts else 0,
                    'pending': status_counts.get('pending', 0) if status_counts else 0,
                    'overdue': status_counts.get('overdue', 0) if status_counts else 0
                },
                'aging_summary': aging_data,
                'top_customers': top_customers,
                'monthly_trends': monthly_trends,
                'payment_efficiency': {
                    'avg_days_to_pay': avg_days,
                    'min_days_to_pay': min_days_str,
                    'max_days_to_pay': max_days_str
                }
            }
        }), 200
        
    except Exception as e:
        print(f"Error generating invoice analytics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/executive-dashboard', methods=['GET'])
@login_required
def get_executive_dashboard():
    """Get all financial data for executive dashboard"""
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        user_id = session['user_id']
        user_role = session.get('role', 'user')
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END) as total_paid,
                    SUM(CASE WHEN status = 'PENDING' THEN total ELSE 0 END) as total_pending,
                    SUM(CASE WHEN status = 'OVERDUE' THEN total ELSE 0 END) as total_overdue,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid_count,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending_count,
                    AVG(DATEDIFF(payment_received_date, upload_date)) as avg_days_to_pay,
                    SUM(total) as total_revenue
                FROM invoices
                WHERE status IN ('PAID', 'PENDING', 'OVERDUE')
            """)
        else:
            cursor.execute("""
                SELECT 
                    SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END) as total_paid,
                    SUM(CASE WHEN status = 'PENDING' THEN total ELSE 0 END) as total_pending,
                    SUM(CASE WHEN status = 'OVERDUE' THEN total ELSE 0 END) as total_overdue,
                    COUNT(CASE WHEN status = 'PAID' THEN 1 END) as paid_count,
                    COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending_count,
                    AVG(DATEDIFF(payment_received_date, upload_date)) as avg_days_to_pay,
                    SUM(total) as total_revenue
                FROM invoices
                WHERE uploaded_by = %s AND status IN ('PAID', 'PENDING', 'OVERDUE')
            """, (user_id,))
        
        metrics = cursor.fetchone()
        

        if user_role == 'admin':
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(payment_received_date, '%%Y-%%m') as month,
                    SUM(total) as revenue
                FROM invoices
                WHERE status = 'PAID' AND payment_received_date >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
                GROUP BY DATE_FORMAT(payment_received_date, '%%Y-%%m')
                ORDER BY month ASC
            """)
        else:
            cursor.execute("""
                SELECT 
                    DATE_FORMAT(payment_received_date, '%%Y-%%m') as month,
                    SUM(total) as revenue
                FROM invoices
                WHERE status = 'PAID' AND uploaded_by = %s AND payment_received_date >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
                GROUP BY DATE_FORMAT(payment_received_date, '%%Y-%%m')
                ORDER BY month ASC
            """, (user_id,))
        
        monthly_revenue = cursor.fetchall()
        

        aging_query = """
            SELECT 
                CASE 
                    WHEN due_date >= CURDATE() OR due_date IS NULL THEN 'Current'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 1 AND 30 THEN '1-30 Days'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 31 AND 60 THEN '31-60 Days'
                    WHEN DATEDIFF(CURDATE(), due_date) BETWEEN 61 AND 90 THEN '61-90 Days'
                    ELSE '90+ Days'
                END as bucket,
                SUM(total) as amount
            FROM invoices
            WHERE status IN ('PENDING', 'OVERDUE')
        """
        
        if user_role != 'admin':
            aging_query += " AND uploaded_by = %s GROUP BY bucket"
            cursor.execute(aging_query, (user_id,))
        else:
            aging_query += " GROUP BY bucket"
            cursor.execute(aging_query)
        
        aging_summary = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'executive_dashboard': {
                'metrics': {
                    'total_revenue': float(metrics.get('total_revenue', 0) or 0),
                    'total_paid': float(metrics.get('total_paid', 0) or 0),
                    'total_pending': float(metrics.get('total_pending', 0) or 0),
                    'total_overdue': float(metrics.get('total_overdue', 0) or 0),
                    'paid_count': metrics.get('paid_count', 0),
                    'pending_count': metrics.get('pending_count', 0),
                    'avg_days_to_pay': round(float(metrics.get('avg_days_to_pay', 0) or 0), 1)
                },
                'monthly_revenue': monthly_revenue,
                'aging_summary': aging_summary,
                'generated_at': datetime.now().isoformat()
            }
        }), 200
        
    except Exception as e:
        print(f"Error generating executive dashboard: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/process-payment', methods=['POST'])
def process_payment():
    """Process payment from payment page"""
    try:
        data = request.json
        token = data.get('token')
        
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT i.*, pt.id as token_id
            FROM invoices i
            JOIN payment_tokens pt ON i.id = pt.invoice_id
            WHERE pt.token = %s AND pt.expires_at > NOW() AND pt.used = 0
        """, (token,))
        
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({'error': 'Payment link expired or invalid'}), 404
        
        if invoice['status'] == 'PAID':
            return jsonify({'error': 'This invoice has already been paid'}), 400
        

        transaction_id = f"mock_{uuid.uuid4().hex[:16]}"
        
        cursor.execute("""
            UPDATE invoices 
            SET status = 'PAID', 
                payment_received_date = NOW(),
                payment_method = 'credit_card',
                payment_transaction_id = %s
            WHERE id = %s
        """, (transaction_id, invoice['id']))
        

        cursor.execute("UPDATE payment_tokens SET used = 1 WHERE id = %s", (invoice['token_id'],))
        
        cursor.execute("""
            INSERT INTO payment_status_log (invoice_id, old_status, new_status, reason)
            VALUES (%s, 'PENDING', 'PAID', 'Payment completed via payment page')
        """, (invoice['id'],))
        
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'success': True,
            'message': 'Payment processed successfully',
            'transaction_id': transaction_id
        }), 200
        
    except Exception as e:
        print(f"Payment processing error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/invoice/<invoice_id>/pdf', methods=['GET'])
@login_required
def generate_invoice_pdf(invoice_id):
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Something went wrong — please try again'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cursor.fetchone()
        if not invoice:
            return jsonify({'error': 'Invoice not found'}), 404
        cursor.execute("SELECT * FROM construction_details WHERE invoice_id = %s", (invoice_id,))
        const_details = cursor.fetchone()
        cursor.execute("SELECT * FROM invoice_items WHERE invoice_id = %s ORDER BY item_number", (invoice_id,))
        items = cursor.fetchall()
        cursor.close()
        connection.close()
        
        log_invoice_action(
            invoice_id, 'DOWNLOAD', session['user_id'], session['username'], session['email'],
            f"Downloaded PDF for invoice {invoice.get('invoice_number', invoice_id)}",
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent')
        )
        
        pdf_filename = f"invoice_{invoice_id}.pdf"
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
        doc = SimpleDocTemplate(pdf_path, pagesize=A4)
        elements = []
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#2563eb'),
            spaceAfter=30
        )
        elements.append(Paragraph("CONSTRUCTION INVOICE", title_style))
        elements.append(Spacer(1, 12))
        
        info_data = [
            ['Invoice Number:', invoice.get('invoice_number', 'N/A')],
            ['Date:', invoice.get('date', 'N/A')],
            ['Due Date:', invoice.get('due_date', 'N/A')],
            ['Status:', invoice.get('status', 'DRAFT')],
        ]
        info_table = Table(info_data, colWidths=[100, 200])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.grey),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 20))
        
        party_data = [
            ['Vendor:', invoice.get('vendor_name', 'N/A')],
            ['Customer:', invoice.get('customer_name', 'N/A')],
        ]
        party_table = Table(party_data, colWidths=[100, 200])
        party_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.grey),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ]))
        elements.append(party_table)
        elements.append(Spacer(1, 20))
        
        if items:
            elements.append(Paragraph("Invoice Items", styles['Heading2']))
            items_data = [['#', 'Description', 'Quantity', 'Unit Price', 'Total']]
            for item in items:
                items_data.append([
                    str(item.get('item_number', '')),
                    item.get('description', ''),
                    str(item.get('quantity', '')),
                    f"${float(item.get('unit_price', 0)):.2f}" if item.get('unit_price') else '-',
                    f"${float(item.get('total', 0)):.2f}" if item.get('total') else '-'
                ])
            items_table = Table(items_data, colWidths=[40, 200, 80, 100, 100])
            items_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            elements.append(items_table)
            elements.append(Spacer(1, 20))
        
        totals_data = []
        if invoice.get('subtotal'):
            totals_data.append(['Subtotal:', f"${float(invoice['subtotal']):.2f}"])
        if invoice.get('tax'):
            totals_data.append(['Tax:', f"${float(invoice['tax']):.2f}"])
        if invoice.get('total'):
            totals_data.append(['Total:', f"${float(invoice['total']):.2f}"])
        if totals_data:
            totals_table = Table(totals_data, colWidths=[400, 100])
            totals_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 12),
                ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                ('LINEABOVE', (0, -1), (-1, -1), 2, colors.black),
            ]))
            elements.append(totals_table)
        
        doc.build(elements)
        return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)
    except Exception as e:
        print(f"PDF generation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Something went wrong — please try again'}), 500
    

@app.route('/cv-files/<path:filename>')
def serve_cv_files(filename):
    """Serve CV files from frontend/cv-files directory"""

    frontend_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend')
    cv_folder = os.path.join(frontend_folder, 'cv-files')
    
    return send_from_directory(cv_folder, filename, as_attachment=True, download_name=filename)
    

@app.route('/contact')
def contact_page():
    """Contact page - clean URL without .html"""
    return render_template('contact.html')

@app.route('/send-contact', methods=['POST'])
def send_contact():
    """Handle contact form submission"""
    try:
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        subject = request.form.get('subject', '').strip()
        message = request.form.get('message', '').strip()
        

        if not name or not email or not subject or not message:
            return redirect('/contact?error=missing_fields')
        
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            return redirect('/contact?error=invalid_email')
        

        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            try:
                cursor.execute("""
                    INSERT INTO contact_messages (name, email, subject, message, created_at)
                    VALUES (%s, %s, %s, %s, NOW())
                """, (name, email, subject, message))
                connection.commit()
            except Error as e:
                print(f"Database error saving contact: {e}")
            finally:
                cursor.close()
                connection.close()
        

        try:
            admin_msg = MIMEMultipart('alternative')
            admin_msg['Subject'] = f'New Contact Form Message: {subject}'
            admin_msg['From'] = f'ConstruInvoice Contact <{SMTP_USER}>'
            admin_msg['To'] = SMTP_USER
            
            admin_text = f"""
New contact form submission:

Name: {name}
Email: {email}
Subject: {subject}

Message:
{message}
"""
            admin_msg.attach(MIMEText(admin_text, 'plain'))
            
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, SMTP_USER, admin_msg.as_string())
        except Exception as e:
            print(f"Admin email error: {e}")
        

        try:
            user_msg = MIMEMultipart('alternative')
            user_msg['Subject'] = 'Thank you for contacting ConstruInvoice'
            user_msg['From'] = f'ConstruInvoice Support <{SMTP_USER}>'
            user_msg['To'] = email
            
            user_text = f"""
Dear {name},

Thank you for contacting ConstruInvoice. We have received your message and will get back to you within 1-2 business days.

Best regards,
ConstruInvoice Support Team
"""
            user_msg.attach(MIMEText(user_text, 'plain'))
            
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, email, user_msg.as_string())
        except Exception as e:
            print(f"Auto-reply email error: {e}")
        
        return redirect('/contact?success=1')
        
    except Exception as e:
        print(f"Contact form error: {e}")
        return redirect('/contact?error=server_error')

@app.route('/app.js')
def serve_app_js():
    return send_from_directory(BASE_DIR, 'app.js')

if __name__ == '__main__':
    print("=" * 60)
    print("Starting Invoice OCR System with Database...")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print(f"Production mode: {PRODUCTION}")
    print("=" * 60)
    
    host = os.environ.get('APP_HOST', '127.0.0.1')
    port = int(os.environ.get('APP_PORT', 5000))
    
    if PRODUCTION:
        app.run(debug=False, host=host, port=port)
    else:
        app.run(debug=True, host=host, port=port)