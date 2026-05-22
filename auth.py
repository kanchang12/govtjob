import os
from functools import wraps
from flask import session, redirect, url_for, request
from supabase import create_client, Client

SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def login_user(email: str, password: str) -> dict:
    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
    return {
        "user":         res.user,
        "access_token": res.session.access_token,
        "refresh_token":res.session.refresh_token,
        "error":        None
    }

def register_user(email: str, password: str, display_name: str) -> dict:
    res = supabase.auth.sign_up({
        "email":    email,
        "password": password,
        "options":  {"data": {"display_name": display_name}}
    })
    return {
        "user":  res.user,
        "error": None
    }

def logout_user():
    supabase.auth.sign_out()

def get_user_from_token(access_token: str):
    try:
        res = supabase.auth.get_user(access_token)
        return res.user
    except Exception:
        return None

def get_display_name(user) -> str:
    if not user:
        return "Anonymous"
    meta = user.user_metadata or {}
    return meta.get("display_name") or user.email.split("@")[0]

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = session.get("access_token")
        if not token:
            return redirect(url_for("login_page", next=request.path))
        user = get_user_from_token(token)
        if not user:
            session.clear()
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated

def current_user():
    token = session.get("access_token")
    if not token:
        return None
    return get_user_from_token(token)

def current_display_name() -> str:
    return get_display_name(current_user())

def current_user_id() -> str:
    u = current_user()
    return str(u.id) if u else ""
