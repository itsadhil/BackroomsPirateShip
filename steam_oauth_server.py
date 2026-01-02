"""
Steam OAuth Web Server
Handles Steam OpenID authentication callbacks
"""

from flask import Flask, request, redirect, render_template_string
from openid.consumer import consumer
from openid.extensions import sreg
import os
import json
import re
from threading import Thread
from steam_utils import SteamLinker

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Store pending authentication sessions
pending_auth = {}

# OAuth callback URL - will be set from env
CALLBACK_URL = os.getenv('STEAM_OAUTH_CALLBACK_URL', 'http://localhost:5000/auth/callback')

SUCCESS_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Steam Login Success</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            text-align: center;
            max-width: 500px;
        }
        h1 {
            color: #4CAF50;
            margin-bottom: 20px;
        }
        .steam-icon {
            font-size: 64px;
            margin-bottom: 20px;
        }
        p {
            color: #666;
            font-size: 16px;
            line-height: 1.6;
        }
        .close-btn {
            margin-top: 20px;
            padding: 10px 30px;
            background: #4CAF50;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
        }
        .close-btn:hover {
            background: #45a049;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="steam-icon">🎮</div>
        <h1>✅ Steam Account Linked!</h1>
        <p>Your Steam account has been successfully linked to your Discord account.</p>
        <p><strong>Steam Name:</strong> {{ steam_name }}</p>
        <p>You can now close this window and return to Discord.</p>
        <button class="close-btn" onclick="window.close()">Close Window</button>
    </div>
</body>
</html>
"""

ERROR_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Steam Login Error</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            text-align: center;
            max-width: 500px;
        }
        h1 {
            color: #f44336;
            margin-bottom: 20px;
        }
        .error-icon {
            font-size: 64px;
            margin-bottom: 20px;
        }
        p {
            color: #666;
            font-size: 16px;
            line-height: 1.6;
        }
        .close-btn {
            margin-top: 20px;
            padding: 10px 30px;
            background: #f44336;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
        }
        .close-btn:hover {
            background: #da190b;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="error-icon">❌</div>
        <h1>Authentication Failed</h1>
        <p>{{ error_message }}</p>
        <p>Please try again or contact support if the issue persists.</p>
        <button class="close-btn" onclick="window.close()">Close Window</button>
    </div>
</body>
</html>
"""

def get_steam_id_from_url(url):
    """Extract Steam ID from OpenID identity URL"""
    match = re.search(r'steamcommunity.com/openid/id/(\d+)', url)
    if match:
        return match.group(1)
    return None

@app.route('/')
def index():
    return "Steam OAuth Server Running"

@app.route('/test')
def test():
    return "Test route works!"

@app.route('/auth/login')
def login():
    """Initiate Steam OpenID authentication"""
    discord_id = request.args.get('discord_id')
    
    if not discord_id:
        return "Missing discord_id parameter", 400
    
    # Store the Discord ID for when they return
    session_id = os.urandom(16).hex()
    pending_auth[session_id] = discord_id
    
    # Build proper realm and return_to URLs
    # realm should be the base URL (e.g., http://localhost:5000)
    realm = f"http://{request.host}"
    return_to = f"{CALLBACK_URL}?session={session_id}"
    
    print(f"[DEBUG] Realm: {realm}")
    print(f"[DEBUG] Return to: {return_to}")
    
    # Build OpenID parameters manually (Steam's stateless OpenID)
    params = {
        'openid.ns': 'http://specs.openid.net/auth/2.0',
        'openid.mode': 'checkid_setup',
        'openid.return_to': return_to,
        'openid.realm': realm,
        'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
    }
    
    # Properly encode and build Steam login URL
    from urllib.parse import urlencode
    steam_login_url = f"https://steamcommunity.com/openid/login?{urlencode(params)}"
    
    print(f"[DEBUG] Redirecting to: {steam_login_url}")
    
    return redirect(steam_login_url)

@app.route('/auth/callback')
def callback():
    """Handle Steam OpenID callback"""
    try:
        print(f"[DEBUG] Callback received with args: {dict(request.args)}")
        
        session_id = request.args.get('session')
        
        if not session_id or session_id not in pending_auth:
            print(f"[ERROR] Invalid session: {session_id}")
            return render_template_string(ERROR_PAGE, error_message="Invalid or expired session.")
        
        discord_id = pending_auth[session_id]
        print(f"[DEBUG] Discord ID: {discord_id}")
        
        # Validate OpenID response manually
        mode = request.args.get('openid.mode')
        print(f"[DEBUG] OpenID mode: {mode}")
        
        if mode == 'cancel':
            return render_template_string(ERROR_PAGE, error_message="You cancelled the Steam login.")
        
        if mode != 'id_res':
            return render_template_string(ERROR_PAGE, error_message="Invalid OpenID response mode.")
        
        # Get claimed identity
        claimed_id = request.args.get('openid.claimed_id', '')
        print(f"[DEBUG] Claimed ID: {claimed_id}")
        
        # Verify the response with Steam
        validation_params = dict(request.args.items())
        validation_params['openid.mode'] = 'check_authentication'
        
        print(f"[DEBUG] Validating with Steam...")
        import requests
        validation_response = requests.post('https://steamcommunity.com/openid/login', data=validation_params, timeout=10)
        print(f"[DEBUG] Steam validation response: {validation_response.text[:200]}")
        
        if 'is_valid:true' not in validation_response.text:
            print(f"[ERROR] Validation failed")
            return render_template_string(ERROR_PAGE, error_message="Steam authentication validation failed.")
        
        # Extract Steam ID from claimed_id
        steam_id = get_steam_id_from_url(claimed_id)
        print(f"[DEBUG] Extracted Steam ID: {steam_id}")
        
        if not steam_id:
            return render_template_string(ERROR_PAGE, error_message="Could not extract Steam ID from response.")
        
        # Link the accounts
        SteamLinker.link_account(discord_id, steam_id)
        print(f"[SUCCESS] Linked Discord {discord_id} to Steam {steam_id}")
        
        # Clean up session
        del pending_auth[session_id]
        
        # Get Steam name
        steam_name = f"Steam User {steam_id}"
        
        return render_template_string(SUCCESS_PAGE, steam_name=steam_name)
    
    except Exception as e:
        import traceback
        print(f"[ERROR] Exception in callback:")
        traceback.print_exc()
        return render_template_string(ERROR_PAGE, error_message=f"Error: {str(e)}")

def run_server(host='0.0.0.0', port=5000):
    """Run the Flask server"""
    app.run(host=host, port=port, debug=False, use_reloader=False)

def start_oauth_server(host='0.0.0.0', port=5000):
    """Start OAuth server in a separate thread"""
    thread = Thread(target=run_server, kwargs={'host': host, 'port': port}, daemon=True)
    thread.start()
    return thread
