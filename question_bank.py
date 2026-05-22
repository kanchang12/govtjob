"""
Question bank - serves questions from Supabase.
No real-time AI generation for free/tier1/tier2.
Real-time AI mentor analysis only for tier3.
"""
import os, json
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

def get_question(question_id: str, language: str = "en") -> dict:
    """Fetch a question with translation if needed."""
    res = supabase.table("questions").select("*").eq("id", question_id).execute()
    if not res.data:
        return {}
    q = res.data[0]

    if language != "en":
        trans = supabase.table("question_translations").select("*")\
                        .eq("question_id", question_id).eq("language", language).execute()
        if trans.data:
            t = trans.data[0]
            q["question"]    = t["question_text"]
            q["option_a"]    = t["option_a"]
            q["option_b"]    = t["option_b"]
            q["option_c"]    = t["option_c"]
            q["option_d"]    = t["option_d"]
            q["explanation"] = t["explanation"]
        else:
            # Fall back to English
            q["question"]    = q["question_en"]
            q["option_a"]    = q["option_a_en"]
            q["option_b"]    = q["option_b_en"]
            q["option_c"]    = q["option_c_en"]
            q["option_d"]    = q["option_d_en"]
            q["explanation"] = q["explanation_en"]
    else:
        q["question"]    = q["question_en"]
        q["option_a"]    = q["option_a_en"]
        q["option_b"]    = q["option_b_en"]
        q["option_c"]    = q["option_c_en"]
        q["option_d"]    = q["option_d_en"]
        q["explanation"] = q["explanation_en"]

    return q

def get_practice_questions(exam_id: str, subject_id: str, topic_id: str,
                           count: int = 10, exclude_ids: list = None,
                           language: str = "en") -> list:
    """Get N practice questions from the bank."""
    q = supabase.table("questions").select("*")\
                .eq("exam_id", exam_id)\
                .eq("subject_id", subject_id)\
                .eq("topic_id", topic_id)\
                .eq("is_verified", True)\
                .eq("is_mock_test", False)\
                .limit(count + len(exclude_ids or []))

    res = q.execute()
    questions = res.data or []

    # Exclude already seen
    if exclude_ids:
        questions = [q for q in questions if q["id"] not in exclude_ids]

    questions = questions[:count]

    # Apply translations
    for i, question in enumerate(questions):
        questions[i] = get_question(question["id"], language)

    return questions

def get_mock_test_questions(mock_test_id: str, language: str = "en") -> list:
    """Get all questions for a specific mock test."""
    res = supabase.table("questions").select("*")\
                  .eq("mock_test_id", mock_test_id)\
                  .eq("is_verified", True)\
                  .execute()
    questions = res.data or []
    return [get_question(q["id"], language) for q in questions]

def get_available_mock_tests(exam_id: str, subject_id: str) -> list:
    res = supabase.table("mock_tests").select("*")\
                  .eq("exam_id", exam_id)\
                  .eq("subject_id", subject_id)\
                  .execute()
    return res.data or []

def get_mentor_feedback(question: dict, user_answer: str, user_id: str) -> str:
    """
    Real-time AI mentor analysis.
    Only called for tier3 users.
    """
    prompt = f"""You are a personal exam mentor. A student got this question wrong.

Question: {question.get('question','')}
A) {question.get('option_a','')}
B) {question.get('option_b','')}
C) {question.get('option_c','')}
D) {question.get('option_d','')}

Student answered: {user_answer}
Correct answer: {question.get('correct_answer','')}
Standard explanation: {question.get('explanation','')}

Give a personal mentor response in 4-5 sentences:
1. What specific mistake the student likely made in their thinking
2. The correct approach step by step
3. A memory technique or shortcut for this type of question
4. What to revise to avoid this mistake in future

Plain text only. No markdown. No asterisks. Direct and practical."""

    r = get_client().models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={"max_output_tokens": 300, "temperature": 0.6}
    )
    return r.text.strip()

def get_basic_feedback(topic_id: str) -> str:
    """Static feedback for free/tier1 users — no API call."""
    return f"Review this topic: {topic_id.replace('_',' ').title()}. Practice more questions in this area."

def calculate_percentile(user_score: float, exam_id: str,
                         subject_id: str, topic_id: str = None) -> float:
    """Calculate percentile from leaderboard data."""
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

def get_question_translated(question_id: str, language: str) -> dict:
    """Get question with on-demand translation and caching."""
    from bulk_generate import translate_on_demand
    if language != "en":
        translate_on_demand(question_id, language)
    return get_question(question_id, language)
