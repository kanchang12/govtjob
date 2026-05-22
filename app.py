import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from models import db, Session, Attempt, TopicStat, Leaderboard
from agent import ask, generate_mock_test, CURRICULUM
from auth import (login_user, register_user, logout_user,
                  login_required, current_user, current_display_name, current_user_id)

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///govtprep.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

def get_subject(subject_id):
    return next((s for s in CURRICULUM["subjects"] if s["id"] == subject_id), {})

def get_exam(exam_id):
    return next((e for e in CURRICULUM["exams"] if e["id"] == exam_id), {})

def get_topic_stat(user_id, exam_id, subject_id, topic_id):
    stat = TopicStat.query.filter_by(user_id=user_id, exam_id=exam_id,
                                     subject_id=subject_id, topic_id=topic_id).first()
    if not stat:
        stat = TopicStat(user_id=user_id, exam_id=exam_id,
                         subject_id=subject_id, topic_id=topic_id)
        db.session.add(stat)
        db.session.commit()
    return stat

# ── Auth routes ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("access_token"):
        return redirect(url_for("home"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        try:
            result = login_user(email, password)
            session["access_token"]  = result["access_token"]
            session["refresh_token"] = result["refresh_token"]
            session["display_name"]  = current_display_name()
            next_url = request.args.get("next", "/")
            return redirect(next_url)
        except Exception as e:
            return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html", error=None)

@app.route("/register", methods=["GET", "POST"])
def register_page():
    if session.get("access_token"):
        return redirect(url_for("home"))
    if request.method == "POST":
        email        = request.form.get("email", "").strip()
        password     = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()
        try:
            register_user(email, password, display_name)
            return render_template("register.html", error=None,
                                   success="Account created! Check your email to confirm, then sign in.")
        except Exception as e:
            err = str(e)
            if "already" in err.lower():
                err = "An account with this email already exists."
            return render_template("register.html", error=err, success=None)
    return render_template("register.html", error=None, success=None)

@app.route("/logout")
def logout():
    try:
        logout_user()
    except Exception:
        pass
    session.clear()
    return redirect(url_for("login_page"))

# ── Pages ─────────────────────────────────────────────────────

@app.route("/")
@login_required
def home():
    return render_template("home.html", exams=CURRICULUM["exams"],
                           display_name=session.get("display_name", ""))

@app.route("/exam/<exam_id>")
@login_required
def exam_page(exam_id):
    exam     = get_exam(exam_id)
    subjects = [s for s in CURRICULUM["subjects"] if exam_id in s.get("exams", [])]
    uid      = current_user_id()
    stats    = {}
    for s in subjects:
        for t in s.get("topics", []):
            stat = TopicStat.query.filter_by(user_id=uid, exam_id=exam_id,
                                             subject_id=s["id"], topic_id=t["id"]).first()
            stats[t["id"]] = stat
    return render_template("exam.html", exam=exam, subjects=subjects, stats=stats,
                           display_name=session.get("display_name", ""))

@app.route("/practice/<exam_id>/<subject_id>/<topic_id>")
@login_required
def practice(exam_id, subject_id, topic_id):
    exam    = get_exam(exam_id)
    subject = get_subject(subject_id)
    topic   = next((t for t in subject.get("topics", []) if t["id"] == topic_id), {})
    stage   = request.args.get("stage", exam.get("stages", ["Prelims"])[0])
    uid     = current_user_id()
    stat    = TopicStat.query.filter_by(user_id=uid, exam_id=exam_id,
                                        subject_id=subject_id, topic_id=topic_id).first()
    leaders = Leaderboard.query.filter_by(exam_id=exam_id, subject_id=subject_id,
                                          topic_id=topic_id)\
                               .order_by(Leaderboard.score_pct.desc(),
                                         Leaderboard.avg_time_secs.asc()).limit(5).all()
    return render_template("practice.html", exam=exam, subject=subject,
                           topic=topic, stage=stage, stat=stat, leaders=leaders,
                           display_name=session.get("display_name", ""))

@app.route("/mock/<exam_id>")
@login_required
def mock_test(exam_id):
    exam     = get_exam(exam_id)
    subjects = [s for s in CURRICULUM["subjects"] if exam_id in s.get("exams", [])]
    pattern  = CURRICULUM.get("mock_test_patterns", {}).get(exam_id + "_prelims", {})
    leaders  = Leaderboard.query.filter_by(exam_id=exam_id, mode="mock")\
                                .order_by(Leaderboard.score_pct.desc()).limit(10).all()
    return render_template("mock.html", exam=exam, subjects=subjects,
                           pattern=pattern, leaders=leaders,
                           display_name=session.get("display_name", ""))

@app.route("/leaderboard")
@login_required
def leaderboard():
    exam_id = request.args.get("exam_id", "")
    if exam_id:
        entries = Leaderboard.query.filter_by(exam_id=exam_id)\
                                   .order_by(Leaderboard.score_pct.desc()).limit(50).all()
    else:
        entries = Leaderboard.query.order_by(Leaderboard.score_pct.desc()).limit(50).all()
    return render_template("leaderboard.html", entries=entries,
                           exams=CURRICULUM["exams"], selected_exam=exam_id,
                           display_name=session.get("display_name", ""))

@app.route("/progress")
@login_required
def progress():
    uid      = current_user_id()
    sessions = Session.query.filter_by(user_id=uid, completed=True)\
                            .order_by(Session.started_at.desc()).limit(30).all()
    stats    = TopicStat.query.filter_by(user_id=uid)\
                              .order_by(TopicStat.last_attempted.desc()).all()
    total_q  = sum(s.questions_total   for s in sessions)
    total_c  = sum(s.questions_correct for s in sessions)
    avg_score = round(total_c / total_q * 100, 1) if total_q else 0
    weak     = sorted([st for st in stats if st.attempts_total >= 3 and st.accuracy_pct < 50],
                      key=lambda x: x.accuracy_pct)
    return render_template("progress.html", sessions=sessions, stats=stats,
                           avg_score=avg_score, total_sessions=len(sessions),
                           weak_topics=weak[:5], display_name=session.get("display_name", ""))

# ── PWA ──────────────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js")

# ── API ───────────────────────────────────────────────────────

@app.route("/api/session/start", methods=["POST"])
@login_required
def start_session():
    d          = request.json
    uid        = current_user_id()
    dname      = session.get("display_name", "User")
    exam_id    = d["exam_id"]
    subject_id = d["subject_id"]
    topic_id   = d["topic_id"]
    stage      = d.get("stage", "Prelims")

    sess = Session(user_id=uid, player_name=dname, exam_id=exam_id,
                   subject_id=subject_id, topic_id=topic_id, stage=stage, mode="practice")
    db.session.add(sess)
    db.session.commit()

    result = ask(exam_id, subject_id, topic_id, stage, [], None)
    return jsonify({"session_id": sess.id, "response": result["response"],
                    "session_complete": result["session_complete"]})

@app.route("/api/session/message", methods=["POST"])
@login_required
def message():
    d          = request.json
    session_id = d["session_id"]
    user_msg   = d.get("message", "").strip()
    history    = d.get("history", [])
    time_secs  = d.get("time_secs")
    uid        = current_user_id()
    dname      = session.get("display_name", "User")

    sess = Session.query.get(session_id)
    if not sess or sess.user_id != uid:
        return jsonify({"error": "Session not found"}), 404

    result = ask(sess.exam_id, sess.subject_id, sess.topic_id,
                 sess.stage, history, user_msg)

    attempt = Attempt(session_id=session_id, topic_id=sess.topic_id,
                      question_text=history[-1]["content"] if history else "",
                      user_answer=user_msg, is_correct=result["is_correct"],
                      time_secs=time_secs)
    db.session.add(attempt)

    if result["is_correct"] is not None:
        sess.questions_total   += 1
        if result["is_correct"]:
            sess.questions_correct += 1
        stat = get_topic_stat(uid, sess.exam_id, sess.subject_id, sess.topic_id)
        stat.update(result["is_correct"], time_secs)

    if result["session_complete"]:
        sess.completed = True
        sess.ended_at  = datetime.utcnow()
        if sess.questions_total:
            sess.score_pct = round(sess.questions_correct / sess.questions_total * 100, 1)
        entry = Leaderboard(
            user_id=uid, player_name=dname,
            exam_id=sess.exam_id, subject_id=sess.subject_id,
            topic_id=sess.topic_id, mode="practice",
            score_pct=sess.score_pct or 0,
            correct=sess.questions_correct, total=sess.questions_total,
            avg_time_secs=time_secs
        )
        db.session.add(entry)

    db.session.commit()

    rank = None
    if result["session_complete"] and sess.score_pct:
        better = Leaderboard.query.filter(
            Leaderboard.exam_id    == sess.exam_id,
            Leaderboard.subject_id == sess.subject_id,
            Leaderboard.topic_id   == sess.topic_id,
            Leaderboard.score_pct  > sess.score_pct
        ).count()
        rank = better + 1

    return jsonify({
        "response": result["response"], "session_complete": result["session_complete"],
        "is_correct": result["is_correct"],
        "score": {"correct": sess.questions_correct, "total": sess.questions_total},
        "rank": rank
    })

@app.route("/api/mock/generate", methods=["POST"])
@login_required
def mock_generate():
    d = request.json
    questions = generate_mock_test(d["exam_id"], d["subject_id"], d.get("num_questions", 10))
    return jsonify({"questions": questions})

@app.route("/api/mock/submit", methods=["POST"])
@login_required
def mock_submit():
    d          = request.json
    uid        = current_user_id()
    dname      = session.get("display_name", "User")
    exam_id    = d["exam_id"]
    subject_id = d["subject_id"]
    correct    = d["correct"]
    total      = d["total"]
    total_secs = d.get("total_secs", 0)
    score_pct  = round(correct / total * 100, 1) if total else 0
    avg_time   = round(total_secs / total, 1) if total else 0

    sess = Session(user_id=uid, player_name=dname, exam_id=exam_id,
                   subject_id=subject_id, mode="mock", completed=True,
                   ended_at=datetime.utcnow(), questions_total=total,
                   questions_correct=correct, score_pct=score_pct, avg_time_secs=avg_time)
    db.session.add(sess)
    entry = Leaderboard(user_id=uid, player_name=dname, exam_id=exam_id,
                        subject_id=subject_id, mode="mock", score_pct=score_pct,
                        correct=correct, total=total, avg_time_secs=avg_time)
    db.session.add(entry)
    db.session.commit()

    better = Leaderboard.query.filter(
        Leaderboard.exam_id    == exam_id,
        Leaderboard.subject_id == subject_id,
        Leaderboard.mode       == "mock",
        Leaderboard.score_pct  > score_pct
    ).count()
    return jsonify({"score_pct": score_pct, "rank": better + 1})

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5001)

# ── Admin auth helper ─────────────────────────────────────────

ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "admin@govtprep.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ── Admin routes ──────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
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
    from sqlalchemy import func, distinct

    total_sessions = Session.query.filter_by(completed=True).count()
    sessions_today = Session.query.filter(
        Session.completed == True,
        Session.started_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
    ).count()

    all_sessions  = Session.query.filter_by(completed=True).all()
    scores        = [s.score_pct for s in all_sessions if s.score_pct]
    avg_score     = round(sum(scores) / len(scores), 1) if scores else 0
    unique_users  = len(set(s.user_id for s in all_sessions))

    recent_sessions = Session.query.filter_by(completed=True)\
                                   .order_by(Session.started_at.desc()).limit(15).all()

    top_performers = Leaderboard.query.order_by(Leaderboard.score_pct.desc()).limit(10).all()

    # Exam stats
    exam_map = {}
    for s in all_sessions:
        if s.exam_id not in exam_map:
            exam_map[s.exam_id] = {"count": 0, "scores": [], "users": set()}
        exam_map[s.exam_id]["count"]  += 1
        exam_map[s.exam_id]["users"].add(s.user_id)
        if s.score_pct:
            exam_map[s.exam_id]["scores"].append(s.score_pct)

    exam_stats = []
    for eid, data in sorted(exam_map.items(), key=lambda x: -x[1]["count"]):
        avg = round(sum(data["scores"]) / len(data["scores"]), 1) if data["scores"] else 0
        exam_stats.append({"exam_id": eid, "count": data["count"],
                           "avg": avg, "users": len(data["users"])})

    return render_template("admin/dashboard.html",
        stats={"total_users": unique_users, "total_sessions": total_sessions,
               "sessions_today": sessions_today, "avg_score": avg_score},
        recent_sessions=recent_sessions,
        top_performers=top_performers,
        exam_stats=exam_stats
    )

@app.route("/admin/users")
@admin_required
def admin_users():
    all_sessions = Session.query.filter_by(completed=True).all()
    user_map = {}
    for s in all_sessions:
        uid = s.user_id
        if uid not in user_map:
            user_map[uid] = {"name": s.player_name, "email": "",
                             "sessions": 0, "scores": [], "last_active": None}
        user_map[uid]["sessions"] += 1
        if s.score_pct:
            user_map[uid]["scores"].append(s.score_pct)
        if not user_map[uid]["last_active"] or s.started_at > user_map[uid]["last_active"]:
            user_map[uid]["last_active"] = s.started_at

    users = []
    for uid, data in user_map.items():
        avg = round(sum(data["scores"]) / len(data["scores"]), 1) if data["scores"] else None
        users.append({"name": data["name"], "email": uid[:12] + "...",
                      "sessions": data["sessions"], "avg_score": avg,
                      "last_active": data["last_active"]})
    users.sort(key=lambda x: -(x["sessions"]))

    return render_template("admin/users.html", users=users)

@app.route("/admin/sessions")
@admin_required
def admin_sessions():
    exam_filter = request.args.get("exam", "")
    q = Session.query.filter_by(completed=True)
    if exam_filter:
        q = q.filter_by(exam_id=exam_filter)
    sessions = q.order_by(Session.started_at.desc()).limit(100).all()
    return render_template("admin/sessions.html", sessions=sessions,
                           exams=CURRICULUM["exams"], exam_filter=exam_filter)

@app.route("/admin/leaderboard")
@admin_required
def admin_leaderboard():
    entries = Leaderboard.query.order_by(Leaderboard.score_pct.desc()).limit(100).all()
    return render_template("leaderboard.html", entries=entries,
                           exams=CURRICULUM["exams"], selected_exam="",
                           display_name="Admin")
