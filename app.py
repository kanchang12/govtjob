import os, json, hmac, hashlib, random, string
from datetime import datetime
from flask import (Flask, request, jsonify, render_template,
                   send_from_directory, session, redirect, url_for)
from flask_cors import CORS
from dotenv import load_dotenv
from auth import (login_user, register_user, logout_user,
                  login_required, current_user, current_display_name, current_user_id)
from tiers import (get_tier_limits, check_usage_allowed, add_seconds_used,
                   apply_referral, get_referral_code, activate_tier,
                   can_use_mentor, can_use_languages, can_use_percentile,
                   get_subscription, TIER_PRICES)
from question_bank import (get_questions_for_session, get_mock_test_questions,
                           get_available_mock_tests, get_mentor_feedback,
                           get_basic_feedback, calculate_percentile,
                           ensure_mock_test_exists, get_answer)

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///govtprep.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

from supabase import create_client
db = create_client(os.environ.get("SUPABASE_URL",""),
                   os.environ.get("SUPABASE_ANON_KEY",""))

CURRICULUM_PATH = os.path.join(os.path.dirname(__file__), "curriculum.json")
with open(CURRICULUM_PATH, encoding="utf-8") as f:
    CURRICULUM = json.load(f)

UPI_ID             = os.environ.get("UPI_ID", "")
GOOGLE_MERCHANT_ID = os.environ.get("GOOGLE_MERCHANT_ID", "")
ADMIN_EMAIL        = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD     = os.environ.get("ADMIN_PASSWORD", "")

SUPPORTED_LANGUAGES = {
    "en": "English", "hi": "Hindi",    "bn": "Bengali",
    "pa": "Punjabi", "ta": "Tamil",    "te": "Telugu",
    "kn": "Kannada", "ml": "Malayalam","or": "Odia"
}

def get_subject(subject_id):
    return next((s for s in CURRICULUM["subjects"] if s["id"] == subject_id), {})

def get_exam(exam_id):
    return next((e for e in CURRICULUM["exams"] if e["id"] == exam_id), {})

def get_user_language(uid):
    sub = get_subscription(uid)
    lang = sub.get("preferred_language", "en")
    if lang != "en" and not can_use_languages(uid):
        return "en"
    return lang

# ── Auth ──────────────────────────────────────────────────────

@app.route("/login", methods=["GET","POST"])
def login_page():
    if session.get("access_token"):
        return redirect(url_for("home"))
    if request.method == "POST":
        email    = request.form.get("email","").strip()
        password = request.form.get("password","")
        try:
            result = login_user(email, password)
            session["access_token"]  = result["access_token"]
            session["refresh_token"] = result["refresh_token"]
            session["display_name"]  = current_display_name()
            return redirect(request.args.get("next", "/"))
        except:
            return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html", error=None)

@app.route("/register", methods=["GET","POST"])
def register_page():
    if session.get("access_token"):
        return redirect(url_for("home"))
    if request.method == "POST":
        email        = request.form.get("email","").strip()
        password     = request.form.get("password","")
        display_name = request.form.get("display_name","").strip()
        referral     = request.form.get("referral_code","").strip().upper()
        try:
            result = register_user(email, password, display_name)
            # Apply referral if provided
            if referral and result.get("user"):
                uid = str(result["user"].id)
                apply_referral(referral, uid)
            return render_template("register.html", error=None,
                success="Account created! You have 7 days free trial. Check email to confirm, then sign in.")
        except Exception as e:
            err = str(e)
            if "already" in err.lower():
                err = "Email already registered."
            return render_template("register.html", error=err, success=None)
    ref = request.args.get("ref","")
    return render_template("register.html", error=None, success=None, referral_code=ref)

@app.route("/logout")
def logout():
    try: logout_user()
    except: pass
    session.clear()
    return redirect(url_for("login_page"))

# ── Pages ─────────────────────────────────────────────────────

@app.route("/")
@login_required
def home():
    uid  = current_user_id()
    sub  = get_subscription(uid)
    usage = check_usage_allowed(uid)
    ref_code = get_referral_code(uid)
    return render_template("home.html",
        exams=CURRICULUM["exams"],
        display_name=session.get("display_name",""),
        tier=sub.get("tier","free"),
        usage=usage,
        ref_code=ref_code,
        ref_link=request.host_url + "register?ref=" + ref_code
    )

@app.route("/exam/<exam_id>")
@login_required
def exam_page(exam_id):
    uid      = current_user_id()
    exam     = get_exam(exam_id)
    subjects = [s for s in CURRICULUM["subjects"] if exam_id in s.get("exams",[])]
    sub      = get_subscription(uid)
    stats    = {}
    for s in subjects:
        for t in s.get("topics", []):
            stat = db.table("topic_stats").select("*")                     .eq("user_id", uid).eq("exam_id", exam_id)                     .eq("subject_id", s["id"]).eq("topic_id", t["id"])                     .execute()
            stats[t["id"]] = stat.data[0] if stat.data else None
    return render_template("exam.html", exam=exam, subjects=subjects, stats=stats,
        display_name=session.get("display_name",""), tier=sub.get("tier","free"))

@app.route("/practice/<exam_id>/<subject_id>/<topic_id>")
@login_required
def practice(exam_id, subject_id, topic_id):
    uid    = current_user_id()
    usage  = check_usage_allowed(uid)
    if not usage["allowed"]:
        return redirect(url_for("upgrade_page", reason="limit"))
    exam    = get_exam(exam_id)
    subject = get_subject(subject_id)
    topic   = next((t for t in subject.get("topics",[]) if t["id"] == topic_id), {})
    stage   = request.args.get("stage", exam.get("stages",["Prelims"])[0])
    lang    = get_user_language(uid)
    sub     = get_subscription(uid)

    # Top 5 leaderboard for this topic
    lb = db.table("leaderboard").select("display_name,score_pct,avg_time_secs")\
           .eq("exam_id", exam_id).eq("subject_id", subject_id)\
           .eq("topic_id", topic_id)\
           .order("score_pct", desc=True).limit(5).execute()

    return render_template("practice.html",
        exam=exam, subject=subject, topic=topic, stage=stage,
        lang=lang, tier=sub.get("tier","free"),
        usage=usage, leaders=lb.data or [],
        display_name=session.get("display_name",""),
        languages=SUPPORTED_LANGUAGES if can_use_languages(uid) else {"en":"English"}
    )

@app.route("/mock/<exam_id>")
@login_required
def mock_test(exam_id):
    uid      = current_user_id()
    usage    = check_usage_allowed(uid)
    if not usage["allowed"]:
        return redirect(url_for("upgrade_page", reason="limit"))
    exam     = get_exam(exam_id)
    subjects = [s for s in CURRICULUM["subjects"] if exam_id in s.get("exams",[])]
    sub      = get_subscription(uid)
    # Load available mock tests
    mock_tests = {}
    for s in subjects:
        tests = get_available_mock_tests(exam_id, s["id"])
        if tests:
            mock_tests[s["id"]] = tests
    lb = db.table("leaderboard").select("display_name,score_pct")\
           .eq("exam_id", exam_id).eq("mode","mock")\
           .order("score_pct", desc=True).limit(10).execute()
    return render_template("mock.html",
        exam=exam, subjects=subjects, mock_tests=mock_tests,
        tier=sub.get("tier","free"), usage=usage,
        leaders=lb.data or [],
        display_name=session.get("display_name","")
    )

@app.route("/leaderboard")
@login_required
def leaderboard():
    exam_id = request.args.get("exam_id","")
    q = db.table("leaderboard").select("*").order("score_pct", desc=True).limit(50)
    if exam_id:
        q = db.table("leaderboard").select("*").eq("exam_id", exam_id)\
              .order("score_pct", desc=True).limit(50)
    entries = q.execute().data or []
    return render_template("leaderboard.html", entries=entries,
        exams=CURRICULUM["exams"], selected_exam=exam_id,
        display_name=session.get("display_name",""))

@app.route("/progress")
@login_required
def progress():
    uid = current_user_id()
    sessions = db.table("sessions").select("*").eq("user_id", uid)\
                 .eq("completed", True).order("started_at", desc=True).limit(30).execute()
    stats    = db.table("topic_stats").select("*").eq("user_id", uid)\
                 .order("last_attempted", desc=True).execute()
    sub      = get_subscription(uid)
    all_s    = sessions.data or []
    total_q  = sum(s.get("questions_total",0)   for s in all_s)
    total_c  = sum(s.get("questions_correct",0) for s in all_s)
    avg_score = round(total_c / total_q * 100, 1) if total_q else 0
    all_stats = stats.data or []
    weak = sorted([st for st in all_stats
                   if st.get("attempts_total",0) >= 3 and st.get("accuracy_pct",0) < 50],
                  key=lambda x: x.get("accuracy_pct",0))
    return render_template("progress.html",
        sessions=all_s, stats=all_stats,
        avg_score=avg_score, total_sessions=len(all_s),
        weak_topics=weak[:5], tier=sub.get("tier","free"),
        display_name=session.get("display_name",""))

@app.route("/upgrade")
@login_required
def upgrade_page():
    reason = request.args.get("reason","")
    uid    = current_user_id()
    sub    = get_subscription(uid)
    ref_code = get_referral_code(uid)
    return render_template("upgrade.html",
        reason=reason, tier=sub.get("tier","free"),
        tier_prices=TIER_PRICES, ref_code=ref_code,
        ref_link=request.host_url + "register?ref=" + ref_code,
        display_name=session.get("display_name",""))

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    uid = current_user_id()
    sub = get_subscription(uid)
    if request.method == "POST":
        lang = request.form.get("language","en")
        if lang != "en" and not can_use_languages(uid):
            lang = "en"
        db.table("subscriptions").update({"preferred_language": lang})\
          .eq("user_id", uid).execute()
        return redirect(url_for("settings"))
    return render_template("settings.html",
        sub=sub, languages=SUPPORTED_LANGUAGES,
        can_change_language=can_use_languages(uid),
        display_name=session.get("display_name",""))

# ── API ───────────────────────────────────────────────────────

@app.route("/api/questions/get", methods=["POST"])
@login_required
def api_get_questions():
    uid  = current_user_id()
    data = request.json
    exam_id    = data["exam_id"]
    subject_id = data["subject_id"]
    topic_id   = data["topic_id"]
    exclude    = data.get("exclude_ids", [])
    lang       = get_user_language(uid)

    usage = check_usage_allowed(uid)
    if not usage["allowed"]:
        return jsonify({"error": "daily_limit", "upgrade_url": "/upgrade?reason=limit"}), 403

    questions = get_questions_for_session(exam_id, subject_id, topic_id,
                                       count=10, exclude_ids=exclude, language=lang)
    if not questions:
        return jsonify({"error": "no_questions", "message": "No questions available for this topic yet."}), 404

    # Strip correct answer from response
    safe = []
    for q in questions:
        safe.append({
            "id":         q["id"],
            "question":   q.get("question",""),
            "option_a":   q.get("option_a",""),
            "option_b":   q.get("option_b",""),
            "option_c":   q.get("option_c",""),
            "option_d":   q.get("option_d",""),
            "difficulty": q.get("difficulty", 3)
        })
    return jsonify({"questions": safe})

@app.route("/api/answer", methods=["POST"])
@login_required
def api_answer():
    uid  = current_user_id()
    data = request.json
    question_id = data["question_id"]
    user_answer = data["user_answer"].upper()
    session_id  = data["session_id"]
    time_secs   = data.get("time_secs", 0)

    # Get correct answer
    res = db.table("questions").select("correct_answer,explanation_en,topic_id")\
            .eq("id", question_id).execute()
    if not res.data:
        return jsonify({"error": "Question not found"}), 404

    q_data     = res.data[0]
    is_correct = user_answer == q_data["correct_answer"]

    # Log attempt
    attempt_row = {
        "session_id":  session_id,
        "user_id":     uid,
        "question_id": question_id,
        "user_answer": user_answer,
        "is_correct":  is_correct,
        "time_secs":   time_secs
    }

    # Mentor feedback for tier3
    feedback = ""
    if not is_correct:
        if can_use_mentor(uid):
            # Get full question for mentor
            full_q = get_answer(question_id) or {}
            feedback = get_mentor_feedback(full_q, user_answer, uid)
            attempt_row["mentor_feedback"] = feedback
        else:
            feedback = get_basic_feedback(q_data.get("topic_id",""))

    db.table("attempts").insert(attempt_row).execute()

    # Update session counts
    sess = db.table("sessions").select("*").eq("id", session_id).execute()
    if sess.data:
        s = sess.data[0]
        new_total   = s.get("questions_total", 0) + 1
        new_correct = s.get("questions_correct", 0) + (1 if is_correct else 0)
        db.table("sessions").update({
            "questions_total":   new_total,
            "questions_correct": new_correct
        }).eq("id", session_id).execute()

    # Update topic stats
    ts = db.table("topic_stats").select("*").eq("user_id", uid)\
           .eq("exam_id", data.get("exam_id",""))\
           .eq("subject_id", data.get("subject_id",""))\
           .eq("topic_id", q_data.get("topic_id","")).execute()
    if ts.data:
        t = ts.data[0]
        new_total_t = t["attempts_total"] + 1
        new_correct_t = t["attempts_correct"] + (1 if is_correct else 0)
        db.table("topic_stats").update({
            "attempts_total":   new_total_t,
            "attempts_correct": new_correct_t,
            "accuracy_pct":     round(new_correct_t/new_total_t*100, 1),
            "last_attempted":   datetime.utcnow().isoformat()
        }).eq("id", t["id"]).execute()
    else:
        db.table("topic_stats").insert({
            "user_id":          uid,
            "exam_id":          data.get("exam_id",""),
            "subject_id":       data.get("subject_id",""),
            "topic_id":         q_data.get("topic_id",""),
            "attempts_total":   1,
            "attempts_correct": 1 if is_correct else 0,
            "accuracy_pct":     100.0 if is_correct else 0.0,
            "last_attempted":   datetime.utcnow().isoformat()
        }).execute()

    # Add usage time
    add_seconds_used(uid, int(time_secs))

    return jsonify({
        "is_correct":     is_correct,
        "correct_answer": q_data["correct_answer"],
        "explanation":    q_data["explanation_en"],
        "feedback":       feedback,
        "show_mentor":    can_use_mentor(uid)
    })

@app.route("/api/session/start", methods=["POST"])
@login_required
def start_session():
    uid  = current_user_id()
    data = request.json

    usage = check_usage_allowed(uid)
    if not usage["allowed"]:
        return jsonify({"error": "daily_limit"}), 403

    sess = db.table("sessions").insert({
        "user_id":    uid,
        "exam_id":    data["exam_id"],
        "subject_id": data["subject_id"],
        "topic_id":   data.get("topic_id"),
        "mode":       data.get("mode","practice"),
        "language":   get_user_language(uid)
    }).execute()

    return jsonify({"session_id": sess.data[0]["id"]})

@app.route("/api/session/complete", methods=["POST"])
@login_required
def complete_session():
    uid  = current_user_id()
    data = request.json
    session_id  = data["session_id"]
    total_secs  = data.get("total_secs", 0)

    sess = db.table("sessions").select("*").eq("id", session_id).execute()
    if not sess.data:
        return jsonify({"error": "not found"}), 404
    s = sess.data[0]

    total_q   = s.get("questions_total", 0)
    correct_q = s.get("questions_correct", 0)
    score_pct = round(correct_q / total_q * 100, 1) if total_q else 0

    db.table("sessions").update({
        "completed":    True,
        "ended_at":     datetime.utcnow().isoformat(),
        "score_pct":    score_pct,
        "duration_secs":total_secs
    }).eq("id", session_id).execute()

    # Save to leaderboard
    dname = session.get("display_name","User")
    lb_row = {
        "user_id":      uid,
        "display_name": dname,
        "exam_id":      s["exam_id"],
        "subject_id":   s["subject_id"],
        "topic_id":     s.get("topic_id"),
        "mock_test_id": s.get("mock_test_id"),
        "mode":         s.get("mode","practice"),
        "score_pct":    score_pct,
        "correct":      correct_q,
        "total":        total_q,
        "avg_time_secs": round(total_secs/total_q, 1) if total_q else 0
    }
    db.table("leaderboard").insert(lb_row).execute()

    # Percentile
    percentile = None
    if can_use_percentile(uid):
        percentile = calculate_percentile(score_pct, s["exam_id"],
                                          s["subject_id"], s.get("topic_id"))

    # Rank
    better = db.table("leaderboard").select("id", count="exact")\
               .eq("exam_id", s["exam_id"]).eq("subject_id", s["subject_id"])\
               .gt("score_pct", score_pct).execute()
    rank = (better.count or 0) + 1

    add_seconds_used(uid, int(total_secs))

    return jsonify({
        "score_pct":  score_pct,
        "correct":    correct_q,
        "total":      total_q,
        "rank":       rank,
        "percentile": percentile
    })

@app.route("/api/mock/questions", methods=["POST"])
@login_required
def api_mock_questions():
    uid  = current_user_id()
    data = request.json
    mock_test_id = data["mock_test_id"]
    lang = get_user_language(uid)

    usage = check_usage_allowed(uid)
    if not usage["allowed"]:
        return jsonify({"error": "daily_limit"}), 403

    questions = get_mock_test_questions(mock_test_id, lang)
    safe = []
    for q in questions:
        safe.append({
            "id":       q["id"],
            "question": q.get("question",""),
            "option_a": q.get("option_a",""),
            "option_b": q.get("option_b",""),
            "option_c": q.get("option_c",""),
            "option_d": q.get("option_d",""),
        })
    return jsonify({"questions": safe, "total": len(safe)})

@app.route("/api/referral/apply", methods=["POST"])
@login_required
def apply_referral_code():
    uid  = current_user_id()
    code = request.json.get("code","").strip().upper()
    if not code:
        return jsonify({"success": False, "error": "No code provided"}), 400
    ok = apply_referral(code, uid)
    if ok:
        return jsonify({"success": True, "message": "7 days added to both accounts!"})
    return jsonify({"success": False, "error": "Invalid or already used code"}), 400

@app.route("/api/usage")
@login_required
def api_usage():
    uid = current_user_id()
    return jsonify(check_usage_allowed(uid))

# ── Payment (GPay) ────────────────────────────────────────────

@app.route("/pricing")
def pricing():
    dn = session.get("display_name","")
    return render_template("pricing.html", display_name=dn, tier_prices=TIER_PRICES)

@app.route("/checkout")
@login_required
def checkout():
    plan = request.args.get("plan","tier1")
    if plan not in TIER_PRICES:
        return redirect(url_for("pricing"))
    p       = TIER_PRICES[plan]
    base    = p["amount"]
    gst     = round(base * 0.18, 2)
    total   = round(base + gst, 2)
    plan_ref = plan[:2].upper() + ''.join(random.choices(string.digits, k=8))
    u = current_user()
    return render_template("checkout.html",
        plan=plan, plan_name=p["label"],
        base=base, gst=gst, amount=total, plan_ref=plan_ref,
        upi_id=UPI_ID, google_merchant_id=GOOGLE_MERCHANT_ID,
        display_name=session.get("display_name","")
    )

@app.route("/api/payment/verify", methods=["POST"])
@login_required
def verify_payment():
    uid     = current_user_id()
    data    = request.json
    plan    = data.get("plan","tier1")
    upi_ref = data.get("upi_ref","")
    print(f"Payment: user={uid} plan={plan} upi_ref={upi_ref}")
    activate_tier(uid, plan, upi_ref)
    return jsonify({"success": True, "plan": plan})

@app.route("/payment/success")
@login_required
def payment_success():
    plan = request.args.get("plan","tier1")
    name = TIER_PRICES.get(plan, {}).get("label","Pro")
    return render_template("payment_success.html", plan=plan, plan_name=name,
                           display_name=session.get("display_name",""))

@app.route("/payment/failed")
@login_required
def payment_failed():
    return render_template("payment_failed.html",
                           display_name=session.get("display_name",""))

# ── PWA and static ────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js")

@app.route("/privacy")
def privacy():
    from datetime import date
    return render_template("privacy.html", date=date.today().strftime("%d %B %Y"))

@app.route("/terms")
def terms():
    from datetime import date
    return render_template("terms.html", date=date.today().strftime("%d %B %Y"))

# ── Admin ─────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        if (request.form.get("email","") == ADMIN_EMAIL and
            request.form.get("password","") == ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin/login.html", error="Invalid credentials.")
    return render_template("admin/login.html", error=None)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    total_q    = db.table("questions").select("id", count="exact").eq("is_verified", True).execute()
    total_sess = db.table("sessions").select("id", count="exact").eq("completed", True).execute()
    total_subs = db.table("subscriptions").select("id", count="exact").execute()
    recent     = db.table("sessions").select("*").eq("completed", True)\
                   .order("started_at", desc=True).limit(20).execute()
    tier_counts = {}
    subs = db.table("subscriptions").select("tier").execute()
    for s in (subs.data or []):
        t = s["tier"]
        tier_counts[t] = tier_counts.get(t,0) + 1

    return render_template("admin/dashboard.html",
        stats={
            "total_questions": total_q.count or 0,
            "total_sessions":  total_sess.count or 0,
            "total_users":     total_subs.count or 0,
        },
        tier_counts=tier_counts,
        recent_sessions=recent.data or []
    )

@app.route("/admin/questions")
@admin_required
def admin_questions():
    q = db.table("questions").select("*").eq("is_verified", True)\
          .order("created_at", desc=True).limit(100).execute()
    return render_template("admin/questions.html",
        questions=q.data or [], exams=CURRICULUM["exams"])

if __name__ == "__main__":
    app.run(debug=True, port=8080)
