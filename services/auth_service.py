import os
import json
import time
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
async def initiate_auth(user_email: str, state: Optional[str] = None):
    """
    Generates the Google OAuth URL for the client to open.
    """
    creds = load_client_secrets()
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline", 
        "prompt": "consent",
        "state": state or user_email,
        "login_hint": user_email
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url, "state": params["state"]}

@router.get("/auth/callback")
async def auth_callback(code: str, state: str, db=Depends(get_db)):
    """
    Handles the OAuth callback, exchanges code for tokens, and stores them.
    """
    try:
        creds = load_client_secrets()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

    # We assume state contains the user_email for mapping
    user_email = state 
    
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token") 
    expires_in = tokens.get("expires_in", 3600)
    expiry = time.time() + expires_in

    await db.execute("""
        INSERT INTO oauth_tokens (user_email, access_token, refresh_token, token_expiry, scope)
        VALUES ($1, $2, $3, to_timestamp($4), $5)
        ON CONFLICT (user_email) 
        DO UPDATE SET 
            access_token = EXCLUDED.access_token,
            refresh_token = COALESCE(EXCLUDED.refresh_token, oauth_tokens.refresh_token),
            token_expiry = EXCLUDED.token_expiry,
            updated_at = CURRENT_TIMESTAMP
    """, user_email, access_token, refresh_token, expiry, " ".join(SCOPES))

    return {"message": "Authentication successful! You can close this window.", "email": user_email}

@router.get("/auth/status")
async def check_auth_status(user_email: str, db=Depends(get_db)):
    """
    Checks if we have a valid token for the user.
    """
    row = await db.fetchrow("SELECT access_token, token_expiry FROM oauth_tokens WHERE user_email = $1", user_email)
    
    if not row:
        return {"authenticated": False}
    
    if row["token_expiry"].timestamp() < time.time() + 30:
         return {"authenticated": False, "expired": True}

    return {"authenticated": True}

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