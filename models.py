from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid

db = SQLAlchemy()

def gen_uuid():
    return str(uuid.uuid4())

class Session(db.Model):
    __tablename__ = 'sessions'
    id               = db.Column(db.String, primary_key=True, default=gen_uuid)
    user_id          = db.Column(db.String, nullable=False)
    player_name      = db.Column(db.String, default="")
    exam_id          = db.Column(db.String, nullable=False)
    subject_id       = db.Column(db.String, nullable=False)
    topic_id         = db.Column(db.String)
    stage            = db.Column(db.String)
    mode             = db.Column(db.String, default="practice")
    started_at       = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at         = db.Column(db.DateTime)
    questions_total  = db.Column(db.Integer, default=0)
    questions_correct= db.Column(db.Integer, default=0)
    score_pct        = db.Column(db.Float)
    avg_time_secs    = db.Column(db.Float)
    total_time_secs  = db.Column(db.Integer)
    completed        = db.Column(db.Boolean, default=False)

class Attempt(db.Model):
    __tablename__ = 'attempts'
    id            = db.Column(db.String, primary_key=True, default=gen_uuid)
    session_id    = db.Column(db.String, db.ForeignKey('sessions.id'), nullable=False)
    topic_id      = db.Column(db.String)
    question_text = db.Column(db.Text)
    user_answer   = db.Column(db.String)
    is_correct    = db.Column(db.Boolean)
    time_secs     = db.Column(db.Float)
    attempted_at  = db.Column(db.DateTime, default=datetime.utcnow)

class TopicStat(db.Model):
    __tablename__ = 'topic_stats'
    id               = db.Column(db.String, primary_key=True, default=gen_uuid)
    user_id          = db.Column(db.String, nullable=False)
    exam_id          = db.Column(db.String, nullable=False)
    subject_id       = db.Column(db.String, nullable=False)
    topic_id         = db.Column(db.String, nullable=False)
    attempts_total   = db.Column(db.Integer, default=0)
    attempts_correct = db.Column(db.Integer, default=0)
    accuracy_pct     = db.Column(db.Float, default=0)
    avg_time_secs    = db.Column(db.Float)
    last_attempted   = db.Column(db.DateTime)
    __table_args__   = (db.UniqueConstraint('user_id', 'exam_id', 'subject_id', 'topic_id'),)

    def update(self, correct, time_secs=None):
        self.attempts_total   += 1
        if correct:
            self.attempts_correct += 1
        self.accuracy_pct   = round(self.attempts_correct / self.attempts_total * 100, 1)
        if time_secs:
            prev = (self.avg_time_secs or 0) * (self.attempts_total - 1)
            self.avg_time_secs = round((prev + time_secs) / self.attempts_total, 1)
        self.last_attempted = datetime.utcnow()

class Leaderboard(db.Model):
    __tablename__ = 'leaderboard'
    id            = db.Column(db.String, primary_key=True, default=gen_uuid)
    user_id       = db.Column(db.String, nullable=False)
    player_name   = db.Column(db.String, nullable=False)
    exam_id       = db.Column(db.String, nullable=False)
    subject_id    = db.Column(db.String, nullable=False)
    topic_id      = db.Column(db.String)
    mode          = db.Column(db.String, default="practice")
    score_pct     = db.Column(db.Float, nullable=False)
    correct       = db.Column(db.Integer)
    total         = db.Column(db.Integer)
    avg_time_secs = db.Column(db.Float)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
