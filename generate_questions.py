"""
Triple-Agent Question Generation Pipeline
=========================================
Run once to populate the question bank.
Each question goes through:
  1. Creator  - generates Q + options + explanation
  2. Critic   - checks for errors, ambiguity, difficulty
  3. Auditor  - final approval as SME

Only verified questions are stored.

Usage:
  python generate_questions.py --exam ibps_po --subject quantitative_aptitude --topic number_system --count 20
  python generate_questions.py --mock --exam ibps_po --subject quantitative_aptitude --count 10 --tests 5
"""

import os, sys, json, argparse, time
from google import genai
from supabase import create_client

client  = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

LANGUAGES = {
    "hi": "Hindi",
    "bn": "Bengali",
    "pa": "Punjabi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "or": "Odia"
}

def call_gemini(prompt, max_tokens=800):
    r = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={"max_output_tokens": max_tokens, "temperature": 0.7}
    )
    return r.text.strip()

# ── Step 1: Creator ──────────────────────────────────────────

def creator(exam_id, subject_id, topic_id, difficulty=3):
    prompt = f"""You are an expert question writer for Indian government competitive exams.

Generate ONE multiple choice question for:
Exam: {exam_id.replace('_',' ').upper()}
Subject: {subject_id.replace('_',' ').title()}
Topic: {topic_id.replace('_',' ').title()}
Difficulty: {difficulty}/5

Output ONLY valid JSON, no markdown, no extra text:
{{
  "question": "...",
  "option_a": "...",
  "option_b": "...",
  "option_c": "...",
  "option_d": "...",
  "correct": "A or B or C or D",
  "explanation": "Full working in plain text, 3-4 sentences, show all steps for maths"
}}

Rules:
- Indian context (INR, Indian institutions, Indian geography)
- No markdown in any field
- Explanation must prove why correct answer is right AND why others are wrong
- Match real exam pattern exactly"""

    raw = call_gemini(prompt)
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── Step 2: Critic ───────────────────────────────────────────

def critic(q_data):
    prompt = f"""You are a strict exam quality controller for Indian government exams.

Review this question critically:

Question: {q_data['question']}
A) {q_data['option_a']}
B) {q_data['option_b']}
C) {q_data['option_c']}
D) {q_data['option_d']}
Correct: {q_data['correct']}
Explanation: {q_data['explanation']}

Check for:
1. Is the correct answer actually correct?
2. Are any wrong options accidentally also correct?
3. Is the question ambiguous or poorly worded?
4. Is the explanation accurate?
5. Is the difficulty appropriate?

Output ONLY valid JSON:
{{
  "approved": true or false,
  "issues": "describe any issues found, or empty string if none",
  "confidence": 1-10
}}"""

    raw = call_gemini(prompt, 300)
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── Step 3: Auditor ──────────────────────────────────────────

def auditor(q_data, critic_feedback):
    prompt = f"""You are the final auditor for an Indian government exam question bank. You are a subject matter expert.

Question has passed initial review with these critic notes: {critic_feedback.get('issues','none')}

Question: {q_data['question']}
A) {q_data['option_a']}
B) {q_data['option_b']}
C) {q_data['option_c']}
D) {q_data['option_d']}
Correct: {q_data['correct']}
Explanation: {q_data['explanation']}

Give final verdict. Output ONLY valid JSON:
{{
  "approved": true or false,
  "reason": "one sentence"
}}"""

    raw = call_gemini(prompt, 200)
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── Step 4: Translate ────────────────────────────────────────

def translate_question(q_data, lang_code, lang_name):
    prompt = f"""Translate this exam question to {lang_name}. Keep technical terms, numbers and proper nouns in original form.

Output ONLY valid JSON, no markdown:
{{
  "question": "...",
  "option_a": "...",
  "option_b": "...",
  "option_c": "...",
  "option_d": "...",
  "explanation": "..."
}}

English original:
Question: {q_data['question']}
A) {q_data['option_a']}
B) {q_data['option_b']}
C) {q_data['option_c']}
D) {q_data['option_d']}
Explanation: {q_data['explanation']}

Translate to {lang_name}. Keep Indian institutions, numbers, formulas in English."""

    raw = call_gemini(prompt, 600)
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── Store in Supabase ────────────────────────────────────────

def store_question(exam_id, subject_id, topic_id, difficulty,
                   q_data, creator_out, critic_out, auditor_out,
                   is_mock=False, mock_test_id=None, translate=False):

    row = {
        "exam_id":       exam_id,
        "subject_id":    subject_id,
        "topic_id":      topic_id,
        "difficulty":    difficulty,
        "question_en":   q_data["question"],
        "option_a_en":   q_data["option_a"],
        "option_b_en":   q_data["option_b"],
        "option_c_en":   q_data["option_c"],
        "option_d_en":   q_data["option_d"],
        "correct_answer":q_data["correct"],
        "explanation_en":q_data["explanation"],
        "is_mock_test":  is_mock,
        "mock_test_id":  mock_test_id,
        "is_verified":   True,
        "creator_output":json.dumps(creator_out),
        "critic_output": json.dumps(critic_out),
        "auditor_output":json.dumps(auditor_out)
    }

    res = supabase.table("questions").insert(row).execute()
    q_id = res.data[0]["id"]
    print(f"  Stored question {q_id}")

    if translate:
        for lang_code, lang_name in LANGUAGES.items():
            try:
                trans = translate_question(q_data, lang_code, lang_name)
                supabase.table("question_translations").insert({
                    "question_id":  q_id,
                    "language":     lang_code,
                    "question_text":trans["question"],
                    "option_a":     trans["option_a"],
                    "option_b":     trans["option_b"],
                    "option_c":     trans["option_c"],
                    "option_d":     trans["option_d"],
                    "explanation":  trans["explanation"]
                }).execute()
                print(f"  Translated to {lang_name}")
                time.sleep(0.5)
            except Exception as e:
                print(f"  Translation to {lang_name} failed: {e}")

    return q_id

# ── Main pipeline ────────────────────────────────────────────

def generate_and_verify(exam_id, subject_id, topic_id, difficulty=3,
                        is_mock=False, mock_test_id=None, translate=False):
    print(f"\nGenerating: {exam_id} / {subject_id} / {topic_id} (diff={difficulty})")

    for attempt in range(3):  # retry up to 3 times
        try:
            # Step 1: Create
            q = creator(exam_id, subject_id, topic_id, difficulty)
            print(f"  Creator OK: {q['question'][:60]}...")

            # Step 2: Critic
            crit = critic(q)
            print(f"  Critic: approved={crit['approved']} confidence={crit.get('confidence','?')}")
            if not crit["approved"] and crit.get("confidence", 10) < 6:
                print(f"  Critic rejected (attempt {attempt+1}). Retrying...")
                time.sleep(1)
                continue

            # Step 3: Auditor
            aud = auditor(q, crit)
            print(f"  Auditor: approved={aud['approved']}")
            if not aud["approved"]:
                print(f"  Auditor rejected (attempt {attempt+1}). Retrying...")
                time.sleep(1)
                continue

            # Passed both — store
            q_id = store_question(exam_id, subject_id, topic_id, difficulty,
                                  q, q, crit, aud, is_mock, mock_test_id, translate)
            return q_id

        except Exception as e:
            print(f"  Error (attempt {attempt+1}): {e}")
            time.sleep(2)

    print("  FAILED after 3 attempts — skipping")
    return None

def create_mock_test(exam_id, subject_id, question_count=10, translate=False):
    """Create one mock test with N verified questions."""
    title = f"{exam_id.upper()} {subject_id.replace('_',' ').title()} Mock"
    res = supabase.table("mock_tests").insert({
        "exam_id":        exam_id,
        "subject_id":     subject_id,
        "title":          title,
        "question_count": question_count,
        "time_mins":      question_count * 2
    }).execute()
    mock_id = res.data[0]["id"]
    print(f"\nMock test created: {mock_id} — {title}")

    stored = 0
    difficulty_cycle = [2, 3, 3, 4, 2, 3, 4, 4, 3, 5]
    for i in range(question_count):
        diff = difficulty_cycle[i % len(difficulty_cycle)]
        q_id = generate_and_verify(exam_id, subject_id, "mixed",
                                   difficulty=diff, is_mock=True,
                                   mock_test_id=mock_id, translate=translate)
        if q_id:
            stored += 1
        time.sleep(1)

    print(f"\nMock test done: {stored}/{question_count} questions stored")
    return mock_id

# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate verified questions")
    parser.add_argument("--exam",    required=True, help="e.g. ibps_po")
    parser.add_argument("--subject", required=True, help="e.g. quantitative_aptitude")
    parser.add_argument("--topic",   default="mixed", help="e.g. number_system")
    parser.add_argument("--count",   type=int, default=10, help="Questions per test/batch")
    parser.add_argument("--tests",   type=int, default=1,  help="Number of mock tests")
    parser.add_argument("--mock",    action="store_true",   help="Generate mock tests")
    parser.add_argument("--translate", action="store_true", help="Translate to all 8 languages")
    args = parser.parse_args()

    if args.mock:
        for i in range(args.tests):
            print(f"\n=== Mock test {i+1}/{args.tests} ===")
            create_mock_test(args.exam, args.subject,
                             args.count, args.translate)
            time.sleep(2)
    else:
        stored = 0
        for i in range(args.count):
            diff = (i % 4) + 2  # cycle 2,3,4,5
            q_id = generate_and_verify(args.exam, args.subject, args.topic,
                                       difficulty=diff, translate=args.translate)
            if q_id:
                stored += 1
            time.sleep(1)
        print(f"\nDone: {stored}/{args.count} questions stored")
