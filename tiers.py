"""
Tier enforcement module.
All tier logic lives here — app.py just calls these functions.
"""
import os
from datetime import datetime, date, timedelta, timezone
from supabase import create_client

supabase = create_client(os.environ.get("SUPABASE_URL",""),
                         os.environ.get("SUPABASE_ANON_KEY",""))

TIER_LIMITS = {
    "trial":  {"seconds_per_day": 7200,  "languages": True,  "mentor": True,   "percentile": True},
    "tier1":  {"seconds_per_day": 3600,  "languages": False, "mentor": False,  "percentile": False},
    "tier2":  {"seconds_per_day": 7200,  "languages": True,  "mentor": False,  "percentile": True},
    "tier3":  {"seconds_per_day": 99999, "languages": True,  "mentor": True,   "percentile": True},
    "free":   {"seconds_per_day": 1800,  "languages": False, "mentor": False,  "percentile": False},
}

TIER_PRICES = {
    "tier1": {"amount": 399,  "label": "Foundation",    "per": "year"},
    "tier2": {"amount": 699,  "label": "Bilingual Pro", "per": "year"},
    "tier3": {"amount": 1999, "label": "Mentor Edge",   "per": "year"},
}

def get_subscription(user_id: str) -> dict:
    res = supabase.table("subscriptions").select("*")\
                  .eq("user_id", user_id).execute()
    if res.data:
        sub = res.data[0]
        # Check if trial/tier has expired
        if sub["tier_expires_at"]:
            expires = datetime.fromisoformat(sub["tier_expires_at"].replace("Z","+00:00"))
            if expires < datetime.now(timezone.utc) and sub["tier"] in ("trial","tier1","tier2","tier3"):
                sub["tier"] = "free"
        return sub
    # Create free subscription on first call
    new_sub = {
        "user_id":       user_id,
        "display_name":  "User",
        "tier":          "trial",
        "tier_expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
        "trial_used":    True
    }
    supabase.table("subscriptions").insert(new_sub).execute()
    return new_sub

def get_tier_limits(user_id: str) -> dict:
    sub  = get_subscription(user_id)
    tier = sub.get("tier", "free")
    return {**TIER_LIMITS.get(tier, TIER_LIMITS["free"]), "tier": tier, "sub": sub}

def get_seconds_used_today(user_id: str) -> int:
    today = date.today().isoformat()
    res   = supabase.table("usage_logs").select("seconds_used")\
                    .eq("user_id", user_id).eq("date", today).execute()
    return res.data[0]["seconds_used"] if res.data else 0

def add_seconds_used(user_id: str, seconds: int):
    today = date.today().isoformat()
    res   = supabase.table("usage_logs").select("*")\
                    .eq("user_id", user_id).eq("date", today).execute()
    if res.data:
        new_total = res.data[0]["seconds_used"] + seconds
        supabase.table("usage_logs")\
                .update({"seconds_used": new_total})\
                .eq("user_id", user_id).eq("date", today).execute()
    else:
        supabase.table("usage_logs")\
                .insert({"user_id": user_id, "date": today, "seconds_used": seconds}).execute()

def check_usage_allowed(user_id: str) -> dict:
    limits      = get_tier_limits(user_id)
    used        = get_seconds_used_today(user_id)
    limit       = limits["seconds_per_day"]
    remaining   = max(0, limit - used)
    allowed     = remaining > 60  # at least 1 minute remaining
    return {
        "allowed":        allowed,
        "seconds_used":   used,
        "seconds_limit":  limit,
        "seconds_remaining": remaining,
        "tier":           limits["tier"]
    }

def apply_referral(referral_code: str, referee_id: str) -> bool:
    """Apply a referral code. Gives both parties 7 days extra."""
    res = supabase.table("referrals").select("*")\
                  .eq("referral_code", referral_code).execute()
    if not res.data:
        return False
    ref = res.data[0]
    if ref.get("rewarded_at"):
        return False  # already used
    if ref["referrer_id"] == referee_id:
        return False  # can't refer yourself

    now = datetime.now(timezone.utc)
    bonus = timedelta(days=7)

    for uid in [ref["referrer_id"], referee_id]:
        sub = get_subscription(uid)
        curr_exp = sub.get("tier_expires_at")
        if curr_exp:
            base = datetime.fromisoformat(curr_exp.replace("Z",""))
            base = max(base, now)
        else:
            base = now
        new_exp = (base + bonus).isoformat()
        new_tier = sub["tier"] if sub["tier"] in ("tier1","tier2","tier3") else "trial"
        supabase.table("subscriptions")\
                .update({"tier_expires_at": new_exp, "tier": new_tier})\
                .eq("user_id", uid).execute()

    supabase.table("referrals")\
            .update({"rewarded_at": now.isoformat()})\
            .eq("referral_code", referral_code).execute()
    return True

def get_referral_code(user_id: str) -> str:
    import hashlib
    return hashlib.md5(user_id.encode()).hexdigest()[:8].upper()

def activate_tier(user_id: str, tier: str, gpay_ref: str):
    expires = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    supabase.table("subscriptions")\
            .update({"tier": tier, "tier_expires_at": expires, "gpay_ref": gpay_ref})\
            .eq("user_id", user_id).execute()

def can_use_languages(user_id: str) -> bool:
    return get_tier_limits(user_id).get("languages", False)

def can_use_mentor(user_id: str) -> bool:
    return get_tier_limits(user_id).get("mentor", False)

def can_use_percentile(user_id: str) -> bool:
    return get_tier_limits(user_id).get("percentile", False)
