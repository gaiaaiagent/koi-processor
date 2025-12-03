"""
RFC 8628 Device Authorization Grant Implementation

Security improvements over previous implementation:
1. user_code: Short code user manually types (prevents phishing)
2. state_id: Opaque ID for OAuth state (device_code never sent to Google)
3. POST endpoints: Secrets in body, not URL (prevents logging exposure)
"""

import os
import json
import time
import secrets
import hashlib
import httpx
import urllib.parse
import string
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Header, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
import asyncpg
from loguru import logger

# Configuration
CLIENT_SECRET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "client_secret.json")
REDIRECT_URI = "https://regen.gaiaai.xyz/api/koi/auth/callback"
VERIFICATION_URI = "https://regen.gaiaai.xyz/activate"
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
]

# Lifetimes
SESSION_TOKEN_LIFETIME_SECONDS = 3600  # 1 hour
DEVICE_CODE_LIFETIME_SECONDS = 600     # 10 minutes
POLL_INTERVAL_SECONDS = 5              # Recommended poll interval

def hash_token(token: str) -> str:
    """SHA-256 hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()

def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)

def generate_device_code() -> str:
    """Generate a cryptographically secure device code (64 hex chars)."""
    return secrets.token_hex(32)

def generate_state_id() -> str:
    """Generate an opaque state ID for OAuth (never expose device_code to Google)."""
    return secrets.token_urlsafe(32)

def generate_user_code() -> str:
    """
    Generate a human-friendly user code (e.g., "WDJB-QK4Z").
    - 8 characters split by hyphen
    - Uses unambiguous characters (no 0/O, 1/I/L)
    """
    # Unambiguous uppercase letters and digits
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    code = ''.join(secrets.choice(alphabet) for _ in range(8))
    return f"{code[:4]}-{code[4:]}"

router = APIRouter(tags=["auth"])

# Pydantic Models
class DeviceCodeRequest(BaseModel):
    """Request to start device authorization flow."""
    client_id: Optional[str] = None  # Optional client identifier

class DeviceCodeResponse(BaseModel):
    """RFC 8628 Device Authorization Response."""
    device_code: str          # Secret: MCP keeps this, uses to poll
    user_code: str            # Public: User types this at verification_uri
    verification_uri: str     # URL where user enters the code
    expires_in: int           # Seconds until codes expire
    interval: int             # Recommended polling interval

class TokenRequest(BaseModel):
    """Request to exchange device_code for session token."""
    device_code: str
    grant_type: str = "urn:ietf:params:oauth:grant-type:device_code"

class TokenResponse(BaseModel):
    """Successful token response."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int

class TokenErrorResponse(BaseModel):
    """RFC 8628 error response."""
    error: str
    error_description: Optional[str] = None

# DB Dependency
async def get_db():
    dsn = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@localhost:5433/eliza")
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()

def load_client_secrets():
    if not os.path.exists(CLIENT_SECRET_FILE):
        raise FileNotFoundError(f"Client secret file not found at {CLIENT_SECRET_FILE}")

    with open(CLIENT_SECRET_FILE, 'r') as f:
        data = json.load(f)
        creds = data.get('web') or data.get('installed')
        if not creds:
            raise ValueError("Invalid client_secret.json: must contain 'web' or 'installed'")
        return creds


# =============================================================================
# RFC 8628 Device Authorization Endpoints
# =============================================================================

@router.post("/auth/device/code", response_model=DeviceCodeResponse)
async def request_device_code(
    request: DeviceCodeRequest = None,
    db=Depends(get_db)
):
    """
    RFC 8628 Device Authorization Request.

    Returns a device_code (secret) and user_code (public).
    MCP client displays: "Go to {verification_uri} and enter code: {user_code}"

    SECURITY: This prevents phishing because user must manually type a code
    they see on THEIR device. Attacker cannot force victim to type unknown code.
    """
    device_code = generate_device_code()
    user_code = generate_user_code()
    state_id = generate_state_id()
    expires_at = time.time() + DEVICE_CODE_LIFETIME_SECONDS

    # Store the auth request
    await db.execute("""
        INSERT INTO auth_requests (device_code, user_code, state_id, status, expires_at)
        VALUES ($1, $2, $3, 'pending', to_timestamp($4))
    """, device_code, user_code, state_id, expires_at)

    logger.info(f"[Auth] Device code requested, user_code={user_code}")

    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=VERIFICATION_URI,
        expires_in=DEVICE_CODE_LIFETIME_SECONDS,
        interval=POLL_INTERVAL_SECONDS
    )


@router.post("/auth/token")
async def exchange_device_code(
    request: TokenRequest,
    db=Depends(get_db)
):
    """
    RFC 8628 Device Access Token Request.

    MCP client polls this endpoint with device_code until user completes auth.

    SECURITY:
    - POST request keeps device_code out of URL/logs
    - Returns session token ONCE, then marks as 'used'
    """
    device_code = request.device_code

    if not device_code or len(device_code) < 32:
        return TokenErrorResponse(
            error="invalid_request",
            error_description="device_code is required"
        )

    row = await db.fetchrow("""
        SELECT id, user_email, status, session_token, expires_at
        FROM auth_requests
        WHERE device_code = $1
    """, device_code)

    if not row:
        return TokenErrorResponse(
            error="invalid_grant",
            error_description="Invalid or expired device code"
        )

    if row["expires_at"].timestamp() < time.time():
        return TokenErrorResponse(
            error="expired_token",
            error_description="Device code has expired"
        )

    if row["status"] == "pending":
        # User hasn't completed auth yet - keep polling
        return TokenErrorResponse(
            error="authorization_pending",
            error_description="User has not yet completed authorization"
        )

    if row["status"] == "used":
        # Token was already retrieved
        return TokenErrorResponse(
            error="invalid_grant",
            error_description="Token has already been retrieved"
        )

    if row["status"] == "rejected":
        return TokenErrorResponse(
            error="access_denied",
            error_description="Email domain not allowed. Only @regen.network emails are permitted."
        )

    if row["status"] == "authenticated":
        # Success! Return the session token
        plain_token = row["session_token"]

        if not plain_token:
            return TokenErrorResponse(
                error="server_error",
                error_description="Session token not found"
            )

        # Mark as used and NULL out the plain token
        await db.execute("""
            UPDATE auth_requests
            SET status = 'used', used_at = CURRENT_TIMESTAMP, session_token = NULL
            WHERE device_code = $1
        """, device_code)

        logger.info(f"[Auth] Session token retrieved for {row['user_email']}")

        return TokenResponse(
            access_token=plain_token,
            token_type="Bearer",
            expires_in=SESSION_TOKEN_LIFETIME_SECONDS
        )

    # Unknown status
    return TokenErrorResponse(
        error="server_error",
        error_description=f"Unknown status: {row['status']}"
    )


# =============================================================================
# User-Facing Activation Pages
# =============================================================================

ACTIVATE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Activate Device - Regen Network</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a472a 0%, #2d5a3d 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        .logo {{
            text-align: center;
            margin-bottom: 30px;
        }}
        .logo h1 {{
            color: #1a472a;
            font-size: 24px;
        }}
        .logo p {{
            color: #666;
            margin-top: 8px;
        }}
        h2 {{
            color: #333;
            margin-bottom: 20px;
            text-align: center;
        }}
        .instructions {{
            background: #f5f5f5;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 24px;
            color: #555;
            font-size: 14px;
            line-height: 1.5;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 500;
        }}
        input[type="text"] {{
            width: 100%;
            padding: 16px;
            font-size: 24px;
            text-align: center;
            letter-spacing: 4px;
            text-transform: uppercase;
            border: 2px solid #ddd;
            border-radius: 8px;
            outline: none;
            transition: border-color 0.2s;
        }}
        input[type="text"]:focus {{
            border-color: #1a472a;
        }}
        input[type="text"]::placeholder {{
            letter-spacing: 2px;
            font-size: 18px;
        }}
        button {{
            width: 100%;
            padding: 16px;
            background: #1a472a;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}
        button:hover {{
            background: #2d5a3d;
        }}
        .error {{
            background: #fee;
            border: 1px solid #fcc;
            color: #c00;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
        }}
        .footer {{
            text-align: center;
            margin-top: 24px;
            color: #999;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>Regen Network</h1>
            <p>Knowledge Organization Infrastructure</p>
        </div>

        <h2>Activate Your Device</h2>

        <div class="instructions">
            Enter the code displayed in your Claude Code / MCP client to authorize access to private Regen documentation.
        </div>

        {error_html}

        <form method="POST" action="/activate">
            <div class="form-group">
                <label for="code">Device Code</label>
                <input type="text" id="code" name="code" placeholder="XXXX-XXXX"
                       maxlength="9" autocomplete="off" autofocus required
                       pattern="[A-Za-z0-9]{{{{4}}}}-?[A-Za-z0-9]{{{{4}}}}">
            </div>
            <button type="submit">Continue with Google</button>
        </form>

        <div class="footer">
            Only @regen.network email addresses are permitted.
        </div>
    </div>

    <script>
        // Auto-format code input with hyphen
        document.getElementById('code').addEventListener('input', function(e) {{
            let value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
            if (value.length > 4) {{
                value = value.slice(0, 4) + '-' + value.slice(4, 8);
            }}
            e.target.value = value;
        }});
    </script>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Success - Regen Network</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a472a 0%, #2d5a3d 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }}
        .checkmark {{
            width: 80px;
            height: 80px;
            background: #1a472a;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 24px;
        }}
        .checkmark svg {{
            width: 40px;
            height: 40px;
            stroke: white;
            stroke-width: 3;
        }}
        h1 {{
            color: #1a472a;
            margin-bottom: 16px;
        }}
        p {{
            color: #666;
            line-height: 1.6;
        }}
        .email {{
            background: #f5f5f5;
            padding: 12px 20px;
            border-radius: 8px;
            margin: 20px 0;
            font-weight: 500;
            color: #333;
        }}
        .close-hint {{
            margin-top: 24px;
            color: #999;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="checkmark">
            <svg viewBox="0 0 24 24" fill="none">
                <path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        </div>
        <h1>Authentication Successful!</h1>
        <p>You have authorized access to private Regen Network documentation.</p>
        <div class="email">{email}</div>
        <p class="close-hint">You can close this window and return to Claude Code.</p>
    </div>
</body>
</html>
"""


@router.get("/activate", response_class=HTMLResponse)
async def activate_page(error: Optional[str] = None):
    """
    Serve the HTML page where users enter their device code.
    """
    error_html = ""
    if error:
        error_messages = {
            "invalid": "Invalid or expired code. Please check and try again.",
            "expired": "This code has expired. Please request a new one in Claude Code.",
            "used": "This code has already been used.",
        }
        error_html = f'<div class="error">{error_messages.get(error, error)}</div>'

    return ACTIVATE_HTML.format(error_html=error_html)


@router.post("/activate", response_class=HTMLResponse)
async def activate_submit(
    code: str = Form(...),
    db=Depends(get_db)
):
    """
    Validate the user code and redirect to Google OAuth.

    SECURITY: User manually types code from their device, preventing phishing.
    """
    # Normalize code (remove hyphen, uppercase)
    user_code = code.upper().replace("-", "")
    if len(user_code) == 8:
        user_code = f"{user_code[:4]}-{user_code[4:]}"

    # Look up the auth request by user_code
    row = await db.fetchrow("""
        SELECT id, device_code, state_id, status, expires_at
        FROM auth_requests
        WHERE user_code = $1
    """, user_code)

    if not row:
        return RedirectResponse(url="/activate?error=invalid", status_code=303)

    if row["expires_at"].timestamp() < time.time():
        return RedirectResponse(url="/activate?error=expired", status_code=303)

    if row["status"] != "pending":
        return RedirectResponse(url="/activate?error=used", status_code=303)

    # Update status to show user started OAuth
    await db.execute("""
        UPDATE auth_requests SET status = 'authorizing' WHERE id = $1
    """, row["id"])

    # Redirect to Google OAuth with opaque state_id (NOT device_code!)
    creds = load_client_secrets()
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": row["state_id"],  # Opaque ID - device_code never sent to Google!
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    logger.info(f"[Auth] User submitted code {user_code}, redirecting to Google")
    return RedirectResponse(url=auth_url, status_code=303)


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(code: str, state: str, db=Depends(get_db)):
    """
    Handles the OAuth callback from Google.

    SECURITY:
    - state contains opaque state_id (NOT device_code)
    - Looks up auth_request by state_id
    - Google tokens used only for identity verification, not stored
    """
    # Look up auth request by state_id
    auth_request = await db.fetchrow("""
        SELECT id, device_code, user_code, user_email, status, expires_at
        FROM auth_requests
        WHERE state_id = $1
    """, state)

    if not auth_request:
        return HTMLResponse(content="<h1>Error</h1><p>Invalid or expired authorization request.</p>", status_code=400)

    if auth_request['status'] not in ('pending', 'authorizing'):
        return HTMLResponse(content="<h1>Error</h1><p>This authorization has already been completed.</p>", status_code=400)

    if auth_request['expires_at'].timestamp() < time.time():
        return HTMLResponse(content="<h1>Error</h1><p>Authorization request expired. Please try again.</p>", status_code=400)

    try:
        creds = load_client_secrets()
    except Exception as e:
        return HTMLResponse(content=f"<h1>Error</h1><p>Server configuration error.</p>", status_code=500)

    # Exchange code for tokens (to verify identity)
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data)
        if resp.status_code != 200:
            logger.error(f"Token exchange failed: {resp.text}")
            return HTMLResponse(content="<h1>Error</h1><p>Failed to verify with Google.</p>", status_code=400)

        tokens = resp.json()

    # Get user info to verify email
    access_token = tokens["access_token"]
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if userinfo_resp.status_code != 200:
            return HTMLResponse(content="<h1>Error</h1><p>Failed to get user info from Google.</p>", status_code=400)
        userinfo = userinfo_resp.json()

    verified_email = userinfo.get("email", "").lower()

    # Verify it's a @regen.network email
    if not verified_email.endswith("@regen.network"):
        logger.warning(f"[Auth] Rejected non-regen email: {verified_email}")
        await db.execute("UPDATE auth_requests SET status = 'rejected' WHERE id = $1", auth_request['id'])
        return HTMLResponse(
            content="<h1>Access Denied</h1><p>Only @regen.network email addresses are permitted.</p>",
            status_code=403
        )

    # Generate session token and store HASH only in long-lived table
    session_token = generate_session_token()
    session_token_hash = hash_token(session_token)
    session_expiry = time.time() + SESSION_TOKEN_LIFETIME_SECONDS

    # Update auth_request with PLAIN token (temporary) and hash
    await db.execute("""
        UPDATE auth_requests
        SET status = 'authenticated',
            user_email = $2,
            session_token = $3,
            session_token_hash = $4,
            authenticated_at = CURRENT_TIMESTAMP
        WHERE id = $1
    """, auth_request['id'], verified_email, session_token, session_token_hash)

    # Store ONLY hash in session_tokens (long-lived table)
    await db.execute("""
        INSERT INTO session_tokens (token_hash, user_email, expires_at)
        VALUES ($1, $2, to_timestamp($3))
    """, session_token_hash, verified_email, session_expiry)

    logger.info(f"[Auth] Authenticated {verified_email} via user_code {auth_request['user_code']}")

    return SUCCESS_HTML.format(email=verified_email)


# =============================================================================
# Legacy endpoints (kept for backwards compatibility, will be deprecated)
# =============================================================================

@router.get("/auth/initiate")
async def initiate_auth_legacy(
    user_email: str,
    device_code: str,
    db=Depends(get_db)
):
    """
    DEPRECATED: Use POST /auth/device/code instead.

    Kept for backwards compatibility during migration.
    """
    logger.warning("[Auth] Legacy /auth/initiate endpoint used - please migrate to POST /auth/device/code")

    # Generate new-style codes
    user_code = generate_user_code()
    state_id = generate_state_id()
    expires_at = time.time() + DEVICE_CODE_LIFETIME_SECONDS

    await db.execute("""
        INSERT INTO auth_requests (device_code, user_code, state_id, user_email, status, expires_at)
        VALUES ($1, $2, $3, $4, 'pending', to_timestamp($5))
        ON CONFLICT (device_code) DO UPDATE SET
            user_code = EXCLUDED.user_code,
            state_id = EXCLUDED.state_id,
            user_email = EXCLUDED.user_email,
            status = 'pending',
            expires_at = EXCLUDED.expires_at
    """, device_code, user_code, state_id, user_email, expires_at)

    creds = load_client_secrets()
    # Still use state_id, not device_code
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state_id,
        "login_hint": user_email
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

    return {"auth_url": auth_url, "state": state_id}


# =============================================================================
# Authorization Dependency (for protected endpoints)
# =============================================================================

async def get_authorized_user(
    authorization: Optional[str] = Header(None),
    db=Depends(get_db)
) -> str:
    """
    FastAPI dependency that validates session token and returns user email.

    Usage:
        @app.get("/protected")
        async def protected_endpoint(user_email: str = Depends(get_authorized_user)):
            return {"user": user_email}
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    # Extract token from "Bearer <token>" format
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = parts[1]
    token_hash = hash_token(token)

    # Look up token hash in session_tokens table
    row = await db.fetchrow("""
        SELECT user_email, expires_at, revoked_at
        FROM session_tokens
        WHERE token_hash = $1
    """, token_hash)

    if not row:
        raise HTTPException(status_code=401, detail="Invalid session token")

    if row["revoked_at"]:
        raise HTTPException(status_code=401, detail="Session token has been revoked")

    if row["expires_at"].timestamp() < time.time():
        raise HTTPException(status_code=401, detail="Session token has expired")

    return row["user_email"]


@router.get("/auth/status")
async def check_auth_status_legacy(device_code: str, db=Depends(get_db)):
    """
    DEPRECATED: Use POST /auth/token instead.

    Kept for backwards compatibility during migration.
    """
    logger.warning("[Auth] Legacy GET /auth/status endpoint used - please migrate to POST /auth/token")

    # Delegate to the new endpoint logic
    request = TokenRequest(device_code=device_code)
    result = await exchange_device_code(request, db)

    # Convert to old response format
    if isinstance(result, TokenResponse):
        return {
            "status": "authenticated",
            "authenticated": True,
            "session_token": result.access_token
        }
    elif isinstance(result, TokenErrorResponse):
        if result.error == "authorization_pending":
            return {"status": "pending", "authenticated": False}
        elif result.error == "access_denied":
            return {"status": "rejected", "authenticated": False, "message": result.error_description}
        else:
            return {"status": result.error, "authenticated": False, "message": result.error_description}

    return result
