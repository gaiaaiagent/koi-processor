import os
import json
import time
import secrets
import hashlib
import httpx
import urllib.parse
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, Depends, Header
from pydantic import BaseModel
import asyncpg
from loguru import logger

# Configuration
CLIENT_SECRET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "client_secret.json")
REDIRECT_URI = "https://regen.gaiaai.xyz/api/koi/auth/callback"
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
]

# Session token lifetime (1 hour)
SESSION_TOKEN_LIFETIME_SECONDS = 3600

def hash_token(token: str) -> str:
    """SHA-256 hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()

def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)

router = APIRouter(tags=["auth"])

class AuthResponse(BaseModel):
    auth_url: str
    state: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

# DB Dependency
async def get_db():
    # Placeholder: Replace with your actual DB connection logic
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

async def get_authorized_user(
    x_user_email: Optional[str] = Header(None, alias="X-User-Email"),
    db=Depends(get_db)
) -> str:
    """
    Dependency that checks if the user is authenticated.
    If not, it raises a 401 error with the auth URL.
    """
    if not x_user_email:
        # If we don't know the email, we can't look up the token.
        # We ask the client to identify itself (or start auth flow with a new session)
        # For simplicity, we require X-User-Email.
        raise HTTPException(status_code=400, detail="Missing X-User-Email header")

    # Check DB
    row = await db.fetchrow("SELECT access_token, token_expiry, refresh_token FROM oauth_tokens WHERE user_email = $1", x_user_email)
    
    needs_auth = False
    if not row:
        needs_auth = True
    elif row["token_expiry"].timestamp() < time.time() + 60:
        # Try refresh
        if row["refresh_token"]:
            try:
                await refresh_access_token(x_user_email, row["refresh_token"], db)
            except:
                needs_auth = True
        else:
            needs_auth = True
            
    if needs_auth:
        # Generate Auth URL
        creds = load_client_secrets()
        params = {
            "client_id": creds["client_id"],
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": x_user_email,
            "login_hint": x_user_email
        }
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
        
        # Return 401 with the URL
        raise HTTPException(
            status_code=401, 
            detail={
                "message": "Authentication required", 
                "auth_url": auth_url, 
                "poll_url": f"/auth/status?user_email={x_user_email}"
            }
        )
        
    return x_user_email

async def refresh_access_token(user_email: str, refresh_token: str, db):
    creds = load_client_secrets()
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        new_tokens = resp.json()
        
    new_access_token = new_tokens["access_token"]
    new_expiry = time.time() + new_tokens.get("expires_in", 3600)
    
    await db.execute("""
        UPDATE oauth_tokens 
        SET access_token = $1, token_expiry = to_timestamp($2), updated_at = CURRENT_TIMESTAMP
        WHERE user_email = $3
    """, new_access_token, new_expiry, user_email)

@router.get("/auth/initiate", response_model=AuthResponse)
async def initiate_auth(
    user_email: str,
    device_code: str,  # REQUIRED: Client-generated UUID to bind this request
    db=Depends(get_db)
):
    """
    Generates the Google OAuth URL for the client to open.

    SECURITY: Requires device_code to prevent IDOR attacks.
    The device_code binds the MCP client to this auth session.
    Only the client that initiated the request can retrieve the session token.
    """
    if not device_code or len(device_code) < 32:
        raise HTTPException(status_code=400, detail="device_code is required (min 32 chars)")

    # Store the pending auth request
    expires_at = time.time() + 600  # 10 minute expiry for auth flow
    await db.execute("""
        INSERT INTO auth_requests (device_code, user_email, status, expires_at)
        VALUES ($1, $2, 'pending', to_timestamp($3))
        ON CONFLICT (device_code) DO UPDATE SET
            user_email = EXCLUDED.user_email,
            status = 'pending',
            expires_at = EXCLUDED.expires_at,
            created_at = CURRENT_TIMESTAMP
    """, device_code, user_email, expires_at)

    creds = load_client_secrets()
    # SECURITY: state contains device_code (not user_email) to bind callback to this request
    state_data = f"{device_code}:{user_email}"
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state_data,  # Contains device_code for callback binding
        "login_hint": user_email
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    logger.info(f"[Auth] Initiated auth for {user_email} with device_code {device_code[:8]}...")
    return {"auth_url": auth_url, "state": state_data}

@router.get("/auth/callback")
async def auth_callback(code: str, state: str, db=Depends(get_db)):
    """
    Handles the OAuth callback, exchanges code for tokens, and creates session token.

    SECURITY: Extracts device_code from state to bind session token to the original client.
    Google tokens are used only for identity verification and NOT stored long-term.
    """
    # Parse state to extract device_code and user_email
    if ':' not in state:
        raise HTTPException(status_code=400, detail="Invalid state format")

    parts = state.split(':', 1)
    device_code = parts[0]
    claimed_email = parts[1] if len(parts) > 1 else None

    # Verify the auth request exists and is pending
    auth_request = await db.fetchrow("""
        SELECT id, user_email, status, expires_at FROM auth_requests
        WHERE device_code = $1
    """, device_code)

    if not auth_request:
        raise HTTPException(status_code=400, detail="Invalid or expired auth request")

    if auth_request['status'] != 'pending':
        raise HTTPException(status_code=400, detail="Auth request already completed")

    if auth_request['expires_at'].timestamp() < time.time():
        raise HTTPException(status_code=400, detail="Auth request expired")

    try:
        creds = load_client_secrets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
            raise HTTPException(status_code=400, detail="Failed to exchange token")

        tokens = resp.json()

    # Get user info to verify email (don't trust claimed_email)
    access_token = tokens["access_token"]
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        userinfo = userinfo_resp.json()

    verified_email = userinfo.get("email", "").lower()

    # Verify it's a @regen.network email
    if not verified_email.endswith("@regen.network"):
        logger.warning(f"[Auth] Rejected non-regen email: {verified_email}")
        await db.execute("UPDATE auth_requests SET status = 'rejected' WHERE device_code = $1", device_code)
        raise HTTPException(status_code=403, detail="Only @regen.network emails are allowed")

    # Generate session token and store HASH only
    session_token = generate_session_token()
    session_token_hash = hash_token(session_token)
    session_expiry = time.time() + SESSION_TOKEN_LIFETIME_SECONDS

    # Update auth_request with session token hash (mark as authenticated)
    await db.execute("""
        UPDATE auth_requests
        SET status = 'authenticated',
            user_email = $2,
            session_token_hash = $3,
            authenticated_at = CURRENT_TIMESTAMP
        WHERE device_code = $1
    """, device_code, verified_email, session_token_hash)

    # Also store in session_tokens table (with hash)
    await db.execute("""
        INSERT INTO session_tokens (session_token, token_hash, user_email, expires_at)
        VALUES ($1, $2, $3, to_timestamp($4))
    """, session_token, session_token_hash, verified_email, session_expiry)

    # NOTE: We do NOT store Google tokens long-term anymore
    # They were only needed to verify identity, which is now done

    logger.info(f"[Auth] Authenticated {verified_email} via device_code {device_code[:8]}...")
    return {
        "message": "Authentication successful! You can close this window.",
        "email": verified_email
    }

@router.get("/auth/status")
async def check_auth_status(device_code: str, db=Depends(get_db)):
    """
    Polls the auth status using device_code.

    SECURITY: Only the client that initiated the auth (with this device_code)
    can retrieve the session token. This prevents IDOR attacks.

    Returns session_token ONCE when authenticated, then marks as 'used'.
    """
    if not device_code or len(device_code) < 32:
        raise HTTPException(status_code=400, detail="device_code is required")

    row = await db.fetchrow("""
        SELECT id, user_email, status, session_token_hash, expires_at
        FROM auth_requests
        WHERE device_code = $1
    """, device_code)

    if not row:
        return {"status": "not_found", "authenticated": False}

    if row["expires_at"].timestamp() < time.time():
        return {"status": "expired", "authenticated": False}

    if row["status"] == "pending":
        return {"status": "pending", "authenticated": False}

    if row["status"] == "used":
        # Token was already retrieved - don't return it again
        return {"status": "already_retrieved", "authenticated": True, "user_email": row["user_email"]}

    if row["status"] == "authenticated":
        # First time polling after auth - retrieve session token from session_tokens table
        # and mark this auth request as used
        session_row = await db.fetchrow("""
            SELECT session_token FROM session_tokens
            WHERE token_hash = $1 AND expires_at > NOW()
        """, row["session_token_hash"])

        if not session_row:
            return {"status": "error", "authenticated": False, "message": "Session token not found"}

        # Mark as used so token can't be retrieved again
        await db.execute("""
            UPDATE auth_requests
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE device_code = $1
        """, device_code)

        logger.info(f"[Auth] Session token retrieved for {row['user_email']} via device_code {device_code[:8]}...")

        return {
            "status": "authenticated",
            "authenticated": True,
            "user_email": row["user_email"],
            "session_token": session_row["session_token"]  # Plain token, returned ONCE
        }

    if row["status"] == "rejected":
        return {"status": "rejected", "authenticated": False, "message": "Email domain not allowed"}

    return {"status": row["status"], "authenticated": False}

async def get_valid_token(user_email: str, db) -> str:
    """
    Internal utility to get a valid token, refreshing if necessary.
    """
    row = await db.fetchrow("SELECT access_token, refresh_token, token_expiry FROM oauth_tokens WHERE user_email = $1", user_email)
    if not row:
        raise ValueError("User not authenticated")

    if row["token_expiry"].timestamp() > time.time() + 60:
        return row["access_token"]
    
    # Refresh logic
    if not row["refresh_token"]:
        raise ValueError("Token expired and no refresh token available")

    await refresh_access_token(user_email, row["refresh_token"], db)
    
    # Refetch to get new token
    row = await db.fetchrow("SELECT access_token FROM oauth_tokens WHERE user_email = $1", user_email)
    return row["access_token"]