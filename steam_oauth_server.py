"""
Improved Steam OAuth Web Server
Handles Steam OpenID authentication callbacks with better session management
"""
from flask import Flask, request, redirect, render_template_string
import os
import json
import re
import secrets
import time
from threading import Thread
from typing import Dict, Optional
import logging

# Use improved steam linker
from utils.steam_linker import SteamLinker
from config.settings import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_urlsafe(32))

# Store pending authentication sessions with expiration
pending_auth: Dict[str, dict] = {}
SESSION_TIMEOUT = 300  # 5 minutes

def cleanup_expired_sessions():
    """Remove expired sessions."""
    current_time = time.time()
    expired = [
        session_id for session_id, session_data in pending_auth.items()
        if current_time - session_data.get('created_at', 0) > SESSION_TIMEOUT
    ]
    for session_id in expired:
        del pending_auth[session_id]
        logger.debug(f"Removed expired session: {session_id}")

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

def get_steam_id_from_url(url: str) -> Optional[str]:
    """Extract Steam ID from OpenID identity URL."""
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
    """Initiate Steam OpenID authentication."""
    discord_id = request.args.get('discord_id')
    
    if not discord_id:
        return "Missing discord_id parameter", 400
    
    # Cleanup expired sessions
    cleanup_expired_sessions()
    
    # Generate cryptographically secure session ID
    session_id = secrets.token_urlsafe(32)
    pending_auth[session_id] = {
        'discord_id': discord_id,
        'created_at': time.time()
    }
    
    # Build proper realm and return_to URLs
    realm = f"http://{request.host}"
    return_to = f"{settings.STEAM_OAUTH_CALLBACK_URL}?session={session_id}"
    
    logger.info(f"Starting Steam auth for Discord ID: {discord_id}")
    
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
    
    return redirect(steam_login_url)

@app.route('/auth/callback')
def callback():
    """Handle Steam OpenID callback."""
    try:
        cleanup_expired_sessions()
        
        session_id = request.args.get('session')
        
        if not session_id or session_id not in pending_auth:
            logger.warning(f"Invalid session: {session_id}")
            return render_template_string(ERROR_PAGE, error_message="Invalid or expired session.")
        
        session_data = pending_auth[session_id]
        discord_id = session_data['discord_id']
        
        # Validate OpenID response manually
        mode = request.args.get('openid.mode')
        
        if mode == 'cancel':
            del pending_auth[session_id]
            return render_template_string(ERROR_PAGE, error_message="You cancelled the Steam login.")
        
        if mode != 'id_res':
            del pending_auth[session_id]
            return render_template_string(ERROR_PAGE, error_message="Invalid OpenID response mode.")
        
        # Get claimed identity
        claimed_id = request.args.get('openid.claimed_id', '')
        
        # Verify the response with Steam
        validation_params = dict(request.args.items())
        validation_params['openid.mode'] = 'check_authentication'
        
        import requests
        try:
            validation_response = requests.post(
                'https://steamcommunity.com/openid/login',
                data=validation_params,
                timeout=10
            )
            
            if 'is_valid:true' not in validation_response.text:
                logger.error("Steam validation failed")
                del pending_auth[session_id]
                return render_template_string(
                    ERROR_PAGE,
                    error_message="Steam authentication validation failed."
                )
        except Exception as e:
            logger.error(f"Error validating with Steam: {e}", exc_info=True)
            del pending_auth[session_id]
            return render_template_string(
                ERROR_PAGE,
                error_message="Could not verify authentication with Steam."
            )
        
        # Extract Steam ID from claimed_id
        steam_id = get_steam_id_from_url(claimed_id)
        
        if not steam_id:
            del pending_auth[session_id]
            return render_template_string(
                ERROR_PAGE,
                error_message="Could not extract Steam ID from response."
            )
        
        # Link the accounts
        if SteamLinker.link_account(discord_id, steam_id):
            logger.info(f"Successfully linked Discord {discord_id} to Steam {steam_id}")
        else:
            logger.error(f"Failed to save Steam link for {discord_id}")
        
        # Clean up session
        del pending_auth[session_id]
        
        # Get Steam name (optional - could fetch from API)
        steam_name = f"Steam User {steam_id}"
        
        return render_template_string(SUCCESS_PAGE, steam_name=steam_name)
    
    except Exception as e:
        logger.error(f"Exception in callback: {e}", exc_info=True)
        if session_id and session_id in pending_auth:
            del pending_auth[session_id]
        return render_template_string(ERROR_PAGE, error_message=f"Error: {str(e)}")

def run_server(host='0.0.0.0', port=5000):
    """Run the Flask server."""
    app.run(host=host, port=port, debug=False, use_reloader=False)

def start_oauth_server(host='0.0.0.0', port=5000):
    """Start OAuth server in a separate thread."""
    thread = Thread(target=run_server, kwargs={'host': host, 'port': port}, daemon=True)
    thread.start()
    logger.info(f"Steam OAuth server thread started on port {port}")
    return thread

