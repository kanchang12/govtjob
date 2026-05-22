import os, json, time
from google import genai
from supabase import create_client

supabase = create_client(os.environ.get("SUPABASE_URL",""),
                         os.environ.get("SUPABASE_ANON_KEY",""))

_client = None
def get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client

QUESTIONS_PER_TOPIC = 50   # generate this many on first access, serve from cache after

# ── Generate and cache on demand ─────────────────────────────

def _call_gemini(prompt, tokens=600):
    for attempt in range(3):
        try:
            r = get_client().models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={"max_output_tokens": tokens, "temperature": 0.85}
            )
            return r.text.strip()
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
    return None

def _generate_one_question(exam_id, subject_id, topic_id, difficulty):
    prompt = f"""Generate ONE multiple choice question for Indian government competitive exam.
Exam: {exam_id.replace('_',' ').upper()}
Subject: {subject_id.replace('_',' ').title()}
Topic: {topic_id.replace('_',' ').title()}
Difficulty: {difficulty}/5

RULES:
- Indian context only (INR, Indian banks, Indian institutions)
- No markdown, no asterisks anywhere
- Explanation must show full working and why each wrong option is wrong
- Output ONLY valid JSON, nothing else before or after

OUTPUT FORMAT:
{{"question":"the question text here","option_a":"first option","option_b":"second option","option_c":"third option","option_d":"fourth option","correct":"A","explanation":"full working here"}}"""

    raw = _call_gemini(prompt, 500)
    if not raw:
        return None
    try:
        raw = raw.strip()
        # Find JSON object
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        raw = raw[start:end]
        data = json.loads(raw)
        # Validate required fields
        required = ["question","option_a","option_b","option_c","option_d","correct","explanation"]
        if not all(k in data for k in required):
            return None
        if data["correct"].upper() not in ["A","B","C","D"]:
            return None
        return data
    except:
        return None

def _store_question(exam_id, subject_id, topic_id, difficulty, q):
    try:
        res = supabase.table("questions").insert({
            "exam_id":       exam_id,
            "subject_id":    subject_id,
            "topic_id":      topic_id,
            "difficulty":    difficulty,
            "question_en":   q["question"],
            "option_a_en":   q["option_a"],
            "option_b_en":   q["option_b"],
            "option_c_en":   q["option_c"],
            "option_d_en":   q["option_d"],
            "correct_answer":q["correct"].upper(),
            "explanation_en":q["explanation"],
            "is_verified":   True,
            "is_mock_test":  False
        }).execute()
        return res.data[0]["id"] if res.data else None
    except:
        return None

def _ensure_topic_has_questions(exam_id, subject_id, topic_id, min_count=20):
    """
    If fewer than min_count questions exist for this topic, generate more.
    Called in background when user starts a session.
    """
    existing = supabase.table("questions").select("id", count="exact")\
                       .eq("exam_id", exam_id).eq("subject_id", subject_id)\
                       .eq("topic_id", topic_id).eq("is_verified", True)\
                       .eq("is_mock_test", False).execute()

    have = existing.count or 0
    if have >= min_count:
        return have

    need = min_count - have
    diff_cycle = [2, 3, 3, 4, 3, 4, 3, 5, 3, 4]
    stored = 0

    for i in range(need):
        diff = diff_cycle[i % len(diff_cycle)]
        q = _generate_one_question(exam_id, subject_id, topic_id, diff)
        if q:
            _store_question(exam_id, subject_id, topic_id, diff, q)
            stored += 1
        time.sleep(0.5)

    return have + stored

# ── Public API ────────────────────────────────────────────────

def get_questions_for_session(exam_id, subject_id, topic_id,
                               count=10, exclude_ids=None, language="en"):
    """
    Main function called when a user starts a practice session.
    Ensures questions exist, then returns them.
    """
    # Ensure at least 2x count questions exist (background fill)
    _ensure_topic_has_questions(exam_id, subject_id, topic_id, min_count=count * 2)

    # Fetch questions
    q = supabase.table("questions").select("*")\
                .eq("exam_id", exam_id)\
                .eq("subject_id", subject_id)\
                .eq("topic_id", topic_id)\
                .eq("is_verified", True)\
                .eq("is_mock_test", False)\
                .limit(count + len(exclude_ids or []))\
                .execute()

    questions = q.data or []

    # Exclude already seen
    if exclude_ids:
        questions = [q for q in questions if q["id"] not in exclude_ids]

    questions = questions[:count]

    # Apply translation if needed
    if language != "en":
        questions = [_apply_translation(q, language) for q in questions]
    else:
        for q in questions:
            q["question"] = q["question_en"]
            q["option_a"] = q["option_a_en"]
            q["option_b"] = q["option_b_en"]
            q["option_c"] = q["option_c_en"]
            q["option_d"] = q["option_d_en"]
            q["explanation"] = q["explanation_en"]

    return questions

def get_answer(question_id):
    """Get correct answer and explanation for a question."""
    res = supabase.table("questions").select(
        "correct_answer,explanation_en,topic_id,subject_id"
    ).eq("id", question_id).execute()
    return res.data[0] if res.data else None

def get_mock_test_questions(mock_test_id, language="en"):
    """Get questions for a specific mock test."""
    res = supabase.table("questions").select("*")\
                  .eq("mock_test_id", mock_test_id)\
                  .eq("is_verified", True).execute()
    questions = res.data or []
    if language != "en":
        questions = [_apply_translation(q, language) for q in questions]
    else:
        for q in questions:
            q["question"] = q["question_en"]
            q["option_a"] = q["option_a_en"]
            q["option_b"] = q["option_b_en"]
            q["option_c"] = q["option_c_en"]
            q["option_d"] = q["option_d_en"]
            q["explanation"] = q["explanation_en"]
    return questions

def get_available_mock_tests(exam_id, subject_id):
    res = supabase.table("mock_tests").select("*")\
                  .eq("exam_id", exam_id).eq("subject_id", subject_id).execute()
    return res.data or []

def ensure_mock_test_exists(exam_id, subject_id):
    """
    Generate a mock test if none exists for this exam/subject.
    Called when user clicks mock test for the first time.
    """
    existing = supabase.table("mock_tests").select("*")\
                       .eq("exam_id", exam_id).eq("subject_id", subject_id)\
                       .limit(1).execute()
    if existing.data:
        return existing.data[0]["id"]

    # Create mock test
    mt = supabase.table("mock_tests").insert({
        "exam_id":        exam_id,
        "subject_id":     subject_id,
        "title":          f"{exam_id.upper()} {subject_id.replace('_',' ').title()} Mock 1",
        "question_count": 30,
        "time_mins":      60
    }).execute()
    mock_id = mt.data[0]["id"]

    diff_pattern = [2,3,3,4,3,4,3,5,3,4,3,3,4,4,3,5,3,4,3,3,3,4,3,4,3,3,4,5,3,4]
    for i in range(30):
        diff = diff_pattern[i % len(diff_pattern)]
        q = _generate_one_question(exam_id, subject_id, "mixed", diff)
        if q:
            supabase.table("questions").insert({
                "exam_id":       exam_id,
                "subject_id":    subject_id,
                "topic_id":      "mixed",
                "difficulty":    diff,
                "question_en":   q["question"],
                "option_a_en":   q["option_a"],
                "option_b_en":   q["option_b"],
                "option_c_en":   q["option_c"],
                "option_d_en":   q["option_d"],
                "correct_answer":q["correct"].upper(),
                "explanation_en":q["explanation"],
                "is_verified":   True,
                "is_mock_test":  True,
                "mock_test_id":  mock_id
            }).execute()
        time.sleep(0.5)

    return mock_id

def get_mentor_feedback(question_data, user_answer):
    """Real-time AI mentor — only for tier3."""
    prompt = f"""You are a personal exam mentor. A student got this question wrong.

Question: {question_data.get('question_en','')}
A) {question_data.get('option_a_en','')}
B) {question_data.get('option_b_en','')}
C) {question_data.get('option_c_en','')}
D) {question_data.get('option_d_en','')}
Student answered: {user_answer}
Correct: {question_data.get('correct_answer','')}
Explanation: {question_data.get('explanation_en','')}

Give personal mentor response in 3-4 sentences:
1. What mistake the student likely made
2. Correct approach step by step
3. Shortcut or memory trick for this type

Plain text only. No markdown. No asterisks. Direct and practical."""

    return _call_gemini(prompt, 250) or "Review the explanation above and practice similar questions."

def get_basic_feedback(topic_id):
    """Static feedback for free/tier1 — no API call."""
    return f"Review {topic_id.replace('_',' ').title()}. Upgrade to Mentor Edge for detailed personal feedback."

def calculate_percentile(user_score, exam_id, subject_id, topic_id=None):
    q = supabase.table("leaderboard").select("score_pct")\
                .eq("exam_id", exam_id).eq("subject_id", subject_id)
    if topic_id:
        q = q.eq("topic_id", topic_id)
    res = q.execute()
    scores = [r["score_pct"] for r in (res.data or [])]
    if not scores:
        return 100.0
    below = sum(1 for s in scores if s <= user_score)
    return round(below / len(scores) * 100, 1)

def _apply_translation(question, language):
    """Get translation from cache, or generate and cache it."""
    trans = supabase.table("question_translations").select("*")\
                    .eq("question_id", question["id"]).eq("language", language).execute()
    if trans.data:
        t = trans.data[0]
        question["question"]    = t["question_text"]
        question["option_a"]    = t["option_a"]
        question["option_b"]    = t["option_b"]
        question["option_c"]    = t["option_c"]
        question["option_d"]    = t["option_d"]
        question["explanation"] = t["explanation"]
        return question

    lang_names = {
        "hi":"Hindi","bn":"Bengali","pa":"Punjabi","ta":"Tamil",
        "te":"Telugu","kn":"Kannada","ml":"Malayalam","or":"Odia"
    }
    lang_name = lang_names.get(language, language)

    prompt = f"""Translate to {lang_name}. Keep numbers, formulas, institution names in English.
Output ONLY valid JSON:
{{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","explanation":"..."}}

English:
Question: {question['question_en']}
A) {question['option_a_en']}
B) {question['option_b_en']}
C) {question['option_c_en']}
D) {question['option_d_en']}
Explanation: {question['explanation_en']}"""

    raw = _call_gemini(prompt, 500)
    if raw:
        try:
            start = raw.find('{')
            end   = raw.rfind('}') + 1
            t = json.loads(raw[start:end])
            supabase.table("question_translations").insert({
                "question_id":  question["id"],
                "language":     language,
                "question_text":t["question"],
                "option_a":     t["option_a"],
                "option_b":     t["option_b"],
                "option_c":     t["option_c"],
                "option_d":     t["option_d"],
                "explanation":  t["explanation"]
            }).execute()
            question["question"]    = t["question"]
            question["option_a"]    = t["option_a"]
            question["option_b"]    = t["option_b"]
            question["option_c"]    = t["option_c"]
            question["option_d"]    = t["option_d"]
            question["explanation"] = t["explanation"]
            return question
        except:
            pass

    # Fallback to English
    question["question"]    = question["question_en"]
    question["option_a"]    = question["option_a_en"]
    question["option_b"]    = question["option_b_en"]
    question["option_c"]    = question["option_c_en"]
    question["option_d"]    = question["option_d_en"]
    question["explanation"] = question["explanation_en"]
    return question
