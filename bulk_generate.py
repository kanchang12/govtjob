"""
Bulk Question Generation Pipeline
Run this overnight to populate the full question bank.

Usage:
  python bulk_generate.py --exam ibps_po --subject quantitative_aptitude
  python bulk_generate.py --all
  python bulk_generate.py --mocks --exam ibps_po
"""

import os, sys, json, time, argparse
from google import genai
from supabase import create_client

client   = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

# Real exam patterns - questions per subject per stage
EXAM_SUBJECTS = {
    "ibps_po": {
        "prelims": [
            ("quantitative_aptitude", 35, 500),  # (subject, qs_in_exam, bank_target)
            ("reasoning",             35, 500),
            ("english",               30, 400),
        ],
        "mains": [
            ("quantitative_aptitude", 35, 500),
            ("reasoning",             40, 500),
            ("english",               35, 400),
            ("general_awareness",     35, 400),
            ("banking_awareness",     40, 400),
        ]
    },
    "sbi_po": {
        "prelims": [
            ("quantitative_aptitude", 35, 500),
            ("reasoning",             35, 500),
            ("english",               30, 400),
        ],
        "mains": [
            ("quantitative_aptitude", 35, 500),
            ("reasoning",             50, 500),
            ("english",               40, 400),
            ("general_awareness",     40, 400),
            ("banking_awareness",     50, 400),
        ]
    },
    "ibps_clerk": {
        "prelims": [
            ("quantitative_aptitude", 35, 400),
            ("reasoning",             35, 400),
            ("english",               30, 350),
        ],
        "mains": [
            ("quantitative_aptitude", 50, 400),
            ("reasoning",             50, 400),
            ("english",               40, 350),
            ("general_awareness",     50, 350),
            ("computer_knowledge",    50, 300),
        ]
    },
    "ssc_cgl": {
        "tier1": [
            ("quantitative_aptitude", 25, 400),
            ("reasoning",             25, 400),
            ("english",               25, 350),
            ("general_awareness",     25, 350),
        ],
        "tier2": [
            ("quantitative_aptitude", 100, 600),
            ("english",               200, 500),
            ("reasoning",             60,  400),
        ]
    },
    "rbi_grade_b": {
        "phase1": [
            ("general_awareness",     80, 500),
            ("quantitative_aptitude", 30, 400),
            ("reasoning",             60, 400),
            ("english",               30, 350),
        ]
    },
    "rrb_ntpc": {
        "cbt1": [
            ("general_awareness",     40, 400),
            ("quantitative_aptitude", 30, 400),
            ("reasoning",             30, 400),
        ],
        "cbt2": [
            ("general_awareness",     50, 400),
            ("quantitative_aptitude", 35, 400),
            ("reasoning",             35, 400),
        ]
    }
}

TOPICS = {
    "quantitative_aptitude": [
        "number_system","simplification","percentage","profit_loss",
        "ratio_proportion","averages","mixtures_alligation","time_work",
        "time_distance","pipes_cisterns","simple_interest","compound_interest",
        "data_interpretation","quadratic_equations","number_series",
        "permutation_combination","probability","mensuration"
    ],
    "reasoning": [
        "syllogism","coding_decoding","blood_relations","direction_sense",
        "inequalities","seating_arrangement","puzzles","input_output",
        "alphanumeric_series","order_ranking","data_sufficiency","critical_reasoning"
    ],
    "english": [
        "reading_comprehension","cloze_test","error_spotting",
        "sentence_correction","fill_in_blanks","para_jumbles",
        "vocabulary","idioms_phrases","word_usage","sentence_completion"
    ],
    "general_awareness": [
        "current_affairs","indian_economy","indian_polity",
        "history","geography","science_tech","sports","budget_economy"
    ],
    "banking_awareness": [
        "rbi_functions","banking_terms","financial_inclusion",
        "monetary_policy","basel_norms","nbfc","govt_schemes",
        "international_orgs","insurance","capital_markets"
    ],
    "computer_knowledge": [
        "basics","ms_office","networking","security","database","shortcut_keys"
    ]
}

MOCK_QS_PER_TEST = 30
MOCKS_PER_SUBJECT = 10

# ── Gemini call ──────────────────────────────────────────────

def call(prompt, tokens=800):
    for attempt in range(3):
        try:
            r = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={"max_output_tokens": tokens, "temperature": 0.85}
            )
            return r.text.strip()
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                print(f"  Rate limit hit, waiting 60s...")
                time.sleep(60)
            else:
                print(f"  API error: {e}")
                time.sleep(5)
    return None

# ── Check already stored ─────────────────────────────────────

def count_stored(exam_id, subject_id, topic_id=None, is_mock=False):
    q = supabase.table("questions").select("id", count="exact")\
                .eq("exam_id", exam_id)\
                .eq("subject_id", subject_id)\
                .eq("is_verified", True)\
                .eq("is_mock_test", is_mock)
    if topic_id:
        q = q.eq("topic_id", topic_id)
    return q.execute().count or 0

# ── Triple agent ─────────────────────────────────────────────

def creator(exam_id, subject_id, topic_id, difficulty):
    prompt = f"""Generate ONE multiple choice question for Indian government exam.
Exam: {exam_id.replace('_',' ').upper()}
Subject: {subject_id.replace('_',' ').title()}
Topic: {topic_id.replace('_',' ').title()}
Difficulty: {difficulty}/5

Output ONLY valid JSON no markdown:
{{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","correct":"A or B or C or D","explanation":"Full working in plain text 3-4 sentences"}}

Rules: Indian context, no markdown anywhere, show all calculation steps, explanation proves why correct AND why others wrong"""
    raw = call(prompt)
    if not raw:
        return None
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

def critic(q):
    prompt = f"""Review this exam question. Output ONLY JSON:
Q: {q['question']}
A){q['option_a']} B){q['option_b']} C){q['option_c']} D){q['option_d']}
Correct: {q['correct']}
Explanation: {q['explanation']}

{{"approved":true/false,"issues":"describe issues or empty string","confidence":1-10}}"""
    raw = call(prompt, 200)
    if not raw:
        return {"approved": False, "issues": "critic failed", "confidence": 0}
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

def auditor(q, crit):
    prompt = f"""Final audit for exam question bank. Critic notes: {crit.get('issues','none')}
Q: {q['question']}
A){q['option_a']} B){q['option_b']} C){q['option_c']} D){q['option_d']}
Correct: {q['correct']}

Output ONLY JSON: {{"approved":true/false,"reason":"one sentence"}}"""
    raw = call(prompt, 150)
    if not raw:
        return {"approved": False}
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

def store(exam_id, subject_id, topic_id, difficulty, q, crit, aud,
          is_mock=False, mock_test_id=None):
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
        "correct_answer":q["correct"],
        "explanation_en":q["explanation"],
        "is_mock_test":  is_mock,
        "mock_test_id":  mock_test_id,
        "is_verified":   True,
        "creator_output":json.dumps(q),
        "critic_output": json.dumps(crit),
        "auditor_output":json.dumps(aud)
    }).execute()
    return res.data[0]["id"]

def generate_one(exam_id, subject_id, topic_id, difficulty,
                 is_mock=False, mock_test_id=None):
    for attempt in range(3):
        try:
            q    = creator(exam_id, subject_id, topic_id, difficulty)
            if not q:
                continue
            crit = critic(q)
            if not crit.get("approved") and crit.get("confidence", 10) < 5:
                continue
            aud  = auditor(q, crit)
            if not aud.get("approved"):
                continue
            q_id = store(exam_id, subject_id, topic_id, difficulty,
                         q, crit, aud, is_mock, mock_test_id)
            return q_id
        except Exception as e:
            print(f"    Error attempt {attempt+1}: {e}")
            time.sleep(2)
    return None

# ── Build topic practice bank ────────────────────────────────

def build_practice_bank(exam_id, subject_id, target):
    topics = TOPICS.get(subject_id, ["general"])
    qs_per_topic = target // len(topics)
    print(f"\n{'='*60}")
    print(f"Practice bank: {exam_id} / {subject_id}")
    print(f"Target: {target} total ({qs_per_topic} per topic x {len(topics)} topics)")
    print(f"{'='*60}")

    total_stored = 0
    for topic in topics:
        already = count_stored(exam_id, subject_id, topic)
        need    = max(0, qs_per_topic - already)
        print(f"\n  Topic: {topic} — have {already}, need {need} more")
        if need == 0:
            total_stored += already
            continue

        diff_cycle = [2,2,3,3,3,4,3,4,4,5]
        for i in range(need):
            diff = diff_cycle[i % len(diff_cycle)]
            q_id = generate_one(exam_id, subject_id, topic, diff)
            if q_id:
                total_stored += 1
            # Progress
            if (i+1) % 10 == 0:
                print(f"    {i+1}/{need} done for {topic}")
            time.sleep(0.8)  # rate limit buffer

    print(f"\nPractice bank done: {total_stored} questions stored")
    return total_stored

# ── Build mock tests ─────────────────────────────────────────

def build_mock_tests(exam_id, subject_id, num_tests=MOCKS_PER_SUBJECT):
    print(f"\n{'='*60}")
    print(f"Mock tests: {exam_id} / {subject_id} — {num_tests} tests x {MOCK_QS_PER_TEST} questions")
    print(f"{'='*60}")

    for t in range(num_tests):
        # Check if this mock already exists
        existing = supabase.table("mock_tests").select("id")\
                           .eq("exam_id", exam_id).eq("subject_id", subject_id)\
                           .execute()
        if len(existing.data or []) > t:
            print(f"  Mock test {t+1} already exists, skipping")
            continue

        title = f"{exam_id.upper()} {subject_id.replace('_',' ').title()} Mock {t+1}"
        mock  = supabase.table("mock_tests").insert({
            "exam_id":        exam_id,
            "subject_id":     subject_id,
            "title":          title,
            "question_count": MOCK_QS_PER_TEST,
            "time_mins":      MOCK_QS_PER_TEST * 2
        }).execute()
        mock_id = mock.data[0]["id"]
        print(f"\n  Building mock {t+1}/{num_tests}: {title}")

        diff_pattern = [2,3,3,4,3,4,3,5,3,4,3,3,4,4,3,5,3,4,3,3,3,4,3,4,3,3,4,5,3,4]
        stored = 0
        for i in range(MOCK_QS_PER_TEST):
            diff = diff_pattern[i % len(diff_pattern)]
            q_id = generate_one(exam_id, subject_id, "mixed", diff,
                                is_mock=True, mock_test_id=mock_id)
            if q_id:
                stored += 1
            time.sleep(0.8)

        print(f"  Mock {t+1} done: {stored}/{MOCK_QS_PER_TEST} questions")

# ── On-demand translation ────────────────────────────────────

def translate_on_demand(question_id, language):
    """Called when a user first requests a language. Cached forever after."""
    # Check if already translated
    existing = supabase.table("question_translations").select("id")\
                       .eq("question_id", question_id).eq("language", language).execute()
    if existing.data:
        return True

    lang_names = {
        "hi":"Hindi","bn":"Bengali","pa":"Punjabi","ta":"Tamil",
        "te":"Telugu","kn":"Kannada","ml":"Malayalam","or":"Odia"
    }
    lang_name = lang_names.get(language, language)

    # Get English original
    q = supabase.table("questions").select("*").eq("id", question_id).execute()
    if not q.data:
        return False
    q = q.data[0]

    prompt = f"""Translate this exam question to {lang_name}. Keep numbers, formulas, proper nouns, institution names in English.

Output ONLY valid JSON no markdown:
{{"question":"...","option_a":"...","option_b":"...","option_c":"...","option_d":"...","explanation":"..."}}

English:
Question: {q['question_en']}
A) {q['option_a_en']}
B) {q['option_b_en']}
C) {q['option_c_en']}
D) {q['option_d_en']}
Explanation: {q['explanation_en']}"""

    raw = call(prompt, 600)
    if not raw:
        return False
    try:
        raw  = raw.replace("```json","").replace("```","").strip()
        data = json.loads(raw)
        supabase.table("question_translations").insert({
            "question_id":  question_id,
            "language":     language,
            "question_text":data["question"],
            "option_a":     data["option_a"],
            "option_b":     data["option_b"],
            "option_c":     data["option_c"],
            "option_d":     data["option_d"],
            "explanation":  data["explanation"]
        }).execute()
        return True
    except:
        return False

# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",     action="store_true", help="Generate everything")
    parser.add_argument("--exam",    default="",          help="Single exam id")
    parser.add_argument("--subject", default="",          help="Single subject id")
    parser.add_argument("--mocks",   action="store_true", help="Build mock tests only")
    parser.add_argument("--practice",action="store_true", help="Build practice bank only")
    args = parser.parse_args()

    targets = []
    if args.all:
        for exam_id, stages in EXAM_SUBJECTS.items():
            seen = set()
            for stage, subjects in stages.items():
                for subj, qs_in_exam, bank_target in subjects:
                    key = (exam_id, subj)
                    if key not in seen:
                        targets.append((exam_id, subj, bank_target))
                        seen.add(key)
    elif args.exam and args.subject:
        targets = [(args.exam, args.subject, 500)]
    elif args.exam:
        stages = EXAM_SUBJECTS.get(args.exam, {})
        seen = set()
        for stage, subjects in stages.items():
            for subj, qs_in_exam, bank_target in subjects:
                if subj not in seen:
                    targets.append((args.exam, subj, bank_target))
                    seen.add(subj)

    print(f"Targets: {len(targets)} exam/subject combinations")
    print(f"Estimated time: {len(targets) * 45} minutes minimum")
    print("Starting...\n")

    for exam_id, subject_id, target in targets:
        if not args.mocks:
            build_practice_bank(exam_id, subject_id, target)
        if not args.practice:
            build_mock_tests(exam_id, subject_id)
        time.sleep(2)

    print("\nAll done.")
