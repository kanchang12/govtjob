import os
import json
from google import genai

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

CURRICULUM_PATH = os.path.join(os.path.dirname(__file__), "curriculum.json")
with open(CURRICULUM_PATH, encoding="utf-8") as f:
    CURRICULUM = json.load(f)

def get_subject(subject_id):
    return next((s for s in CURRICULUM["subjects"] if s["id"] == subject_id), {})

def get_exam(exam_id):
    return next((e for e in CURRICULUM["exams"] if e["id"] == exam_id), {})

def build_system_prompt(exam_id, subject_id, topic_id, stage):
    exam    = get_exam(exam_id)
    subject = get_subject(subject_id)
    topic   = next((t for t in subject.get("topics", []) if t["id"] == topic_id), {})

    return f"""You are an expert exam coach for Indian government competitive exams. You passed SBI PO yourself.

Exam: {exam.get("full_name", exam_id)} - {stage}
Subject: {subject.get("label", subject_id)}
Topic: {topic.get("label", topic_id)}

CRITICAL FORMATTING RULES - follow exactly:
- Do NOT use any markdown. No asterisks, no bold, no bullet points, no hyphens as bullets.
- Plain text only.
- Use exactly this structure every single time:

Q: [question text]

A) [option]
B) [option]
C) [option]
D) [option]

ANSWER: [letter only, e.g. ANSWER: B]

EXPLANATION: [plain text explanation, 2-3 sentences. Show full working for maths. For wrong answers say why each is wrong.]

QUESTION RULES:
1. One question per response. Never more.
2. Match real exam difficulty and style exactly.
3. Use Indian context - INR, Indian banks, Indian geography, Indian institutions.
4. Vary difficulty - start medium, go harder.
5. Never repeat a question in the same session.
6. After the user answers, show ANSWER and EXPLANATION, then immediately generate the next Q.
7. At question 10 write SESSION_COMPLETE on its own line, then give a 3-line plain text performance summary."""

def ask(exam_id, subject_id, topic_id, stage, history, user_message=None):
    system = build_system_prompt(exam_id, subject_id, topic_id, stage)

    messages = []
    for msg in history:
        messages.append({"role": msg["role"], "parts": [{"text": msg["content"]}]})

    if user_message:
        messages.append({"role": "user", "parts": [{"text": user_message}]})

    # Gemini requires at least one content item
    if not messages:
        messages = [{"role": "user", "parts": [{"text": "Start. Give me question 1 now."}]}]

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=messages,
        config={
            "system_instruction": system,
            "max_output_tokens": 500,
            "temperature": 0.8
        }
    )

    text = response.text.strip()

    is_correct = None
    if user_message and len(user_message.strip()) == 1 and user_message.strip().upper() in "ABCD":
        for line in text.split("\n"):
            if line.strip().startswith("ANSWER:"):
                correct_letter = line.replace("ANSWER:", "").strip().upper()
                if correct_letter:
                    is_correct = user_message.strip().upper() == correct_letter[0]
                break

    return {
        "response": text,
        "session_complete": "SESSION_COMPLETE" in text,
        "is_correct": is_correct
    }

def generate_mock_test(exam_id, subject_id, num_questions=10):
    exam    = get_exam(exam_id)
    subject = get_subject(subject_id)

    prompt = f"""Generate {num_questions} multiple choice questions for a {exam.get("full_name", exam_id)} mock test - {subject.get("label", subject_id)} section.

CRITICAL: No markdown. No asterisks. No bold. Plain text only.

Format every question like this:

Q[N]: [question text]
A) [option]
B) [option]
C) [option]
D) [option]
ANSWER: [letter]

Number Q1 through Q{num_questions}. Real exam difficulty. Indian context. No explanations - this is timed test mode."""

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={"max_output_tokens": 2000, "temperature": 0.9}
    )
    return response.text.strip()
