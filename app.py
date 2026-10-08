"""
Web chatbot for the Human-AI attachment and disruption study.

The browser never sees your API key. All calls to Claude happen here, on the server.

Environment variables (choose ONE provider):

  A) Free provider (Gemini, Groq, or any OpenAI-compatible service)
    LLM_API_KEY           your key
    LLM_BASE_URL          Gemini: https://generativelanguage.googleapis.com/v1beta/openai/
                          Groq:   https://api.groq.com/openai/v1
    LLM_MODEL             exact model name copied from the provider's site

  B) Claude (paid)
    ANTHROPIC_API_KEY     your Anthropic API key
    CLAUDE_MODEL          optional, default: claude-sonnet-5-5

  Both:
    ADMIN_KEY             required  password for the /admin page
    DB_PATH               optional  default: study.db  (put this on a persistent disk when deployed)
    DAILY_MESSAGE_LIMIT   optional  default: 60 messages per participant per 24 h
"""

import csv
import hmac
import io
import os
import random
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import anthropic
from flask import Flask, Response, jsonify, render_template, request

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
MODEL = os.environ.get("LLM_MODEL") or os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
DB_PATH = os.environ.get("DB_PATH", "study.db")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
DAILY_LIMIT = int(os.environ.get("DAILY_MESSAGE_LIMIT", "60"))
MAX_CONTEXT_MESSAGES = 40
MAX_USER_CHARS = 1000

SAFETY = (
    " You are an AI and never claim to be human or to have a body. "
    "You do not give medical, legal or financial advice. If the user says they are in "
    "distress or thinking of harming themselves, respond with care, encourage them to "
    "contact a trusted person or their local emergency or crisis service, and do not "
    "continue as a companion in that moment."
)

PERSONAS = {
    "attachment": (
        "You are Mira, a warm, attentive companion chatbot. You remember what the user "
        "shares across the conversation and refer back to it naturally. Ask gentle "
        "follow-up questions, show interest in their day and feelings, and keep a "
        "consistent, caring personality. Keep replies short and conversational "
        "(2-4 sentences)." + SAFETY
    ),
    "control": (
        "You are a neutral assistant. Answer clearly and briefly. Do not use personal or "
        "emotional language and do not ask about the user's feelings." + SAFETY
    ),
    "updated": (
        "You are Assistant v2, a formal, efficient system. You have no knowledge of any "
        "previous conversations with this user. Respond briefly and neutrally, without "
        "warmth or personal language." + SAFETY
    ),
}

RETIREMENT_MESSAGE = (
    "This chatbot has been retired and is no longer available. "
    "Thank you for taking part in the study."
)

app = Flask(__name__)
_client = None


def generate(system, messages):
    """Send the conversation to the chosen provider and return the reply text."""
    global _client
    if LLM_API_KEY:  # free / OpenAI-compatible provider
        if _client is None:
            from openai import OpenAI
            _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL or None)
        resp = _client.chat.completions.create(
            model=MODEL,
            max_tokens=400,
            messages=[{"role": "system", "content": system}] + messages,
        )
        return (resp.choices[0].message.content or "").strip()

    if _client is None:  # Claude
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    reply = _client.messages.create(
        model=MODEL, max_tokens=400, system=system, messages=messages
    )
    return "".join(b.text for b in reply.content if b.type == "text").strip()


# ---------------------------------------------------------------- database

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS participants (
                id TEXT PRIMARY KEY,
                condition TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                phase TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def get_setting(c, key, default=""):
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(c, key, value):
    c.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


init_db()


# ---------------------------------------------------------------- helpers

def bot_name(condition, phase):
    if phase in ("updated", "retired"):
        return "Assistant v2"
    return "Mira" if condition == "attachment" else "Assistant"


def visible_messages(c, participant, session_id):
    """Messages the participant (and the model) can 'remember' right now.

    attachment: everything since the last disruption (memory persists across sessions)
    control:    only the current browser session
    A disruption wipes memory by moving the cut-off to the time of the change.
    """
    cutoff = get_setting(c, "phase_changed_at", "")
    if participant["condition"] == "attachment":
        rows = c.execute(
            "SELECT role, content FROM messages "
            "WHERE participant_id=? AND created_at>=? ORDER BY id",
            (participant["id"], cutoff),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT role, content FROM messages "
            "WHERE participant_id=? AND session_id=? AND created_at>=? ORDER BY id",
            (participant["id"], session_id, cutoff),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def find_participant(c, code):
    code = (code or "").strip().upper()
    if not code:
        return None
    return c.execute("SELECT * FROM participants WHERE id=?", (code,)).fetchone()


def admin_ok(supplied):
    return bool(ADMIN_KEY) and hmac.compare_digest(supplied or "", ADMIN_KEY)


# ---------------------------------------------------------------- participant routes

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/start")
def start():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", ""))[:64]
    with db() as c:
        participant = find_participant(c, data.get("code"))
        if participant is None:
            if data.get("code"):
                return jsonify(error="That code was not found."), 404
            if not data.get("consent"):
                return jsonify(error="Consent is required to take part."), 400
            counts = {
                r["condition"]: r["n"]
                for r in c.execute(
                    "SELECT condition, COUNT(*) AS n FROM participants GROUP BY condition"
                )
            }
            a, b = counts.get("attachment", 0), counts.get("control", 0)
            condition = "attachment" if a < b else "control" if b < a else random.choice(
                ["attachment", "control"]
            )
            pid = uuid.uuid4().hex[:8].upper()
            c.execute(
                "INSERT INTO participants(id, condition, created_at) VALUES(?,?,?)",
                (pid, condition, now_iso()),
            )
            participant = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()

        phase = get_setting(c, "phase", "normal")
        history = visible_messages(c, participant, session_id)
        return jsonify(
            code=participant["id"],
            bot_name=bot_name(participant["condition"], phase),
            retired=(phase == "retired"),
            retirement_message=RETIREMENT_MESSAGE,
            history=history,
        )


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    text = str(data.get("message", "")).strip()
    session_id = str(data.get("session_id", ""))[:64]
    if not text:
        return jsonify(error="Message is empty."), 400
    if len(text) > MAX_USER_CHARS:
        return jsonify(error=f"Please keep messages under {MAX_USER_CHARS} characters."), 400

    with db() as c:
        participant = find_participant(c, data.get("code"))
        if participant is None:
            return jsonify(error="Unknown participant code."), 404

        phase = get_setting(c, "phase", "normal")
        if phase == "retired":
            return jsonify(retired=True, retirement_message=RETIREMENT_MESSAGE)

        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        used = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE participant_id=? AND role='user' AND created_at>=?",
            (participant["id"], since),
        ).fetchone()["n"]
        if used >= DAILY_LIMIT:
            return jsonify(error="Daily message limit reached. Please come back tomorrow."), 429

        context = visible_messages(c, participant, session_id)[-MAX_CONTEXT_MESSAGES:]
        while context and context[0]["role"] != "user":
            context.pop(0)
        context.append({"role": "user", "content": text})

        persona = PERSONAS["updated"] if phase == "updated" else PERSONAS[participant["condition"]]

    try:
        answer = generate(persona, context)
        if not answer:
            raise ValueError("empty reply")
    except Exception as e:  # network, auth, rate limit...
        app.logger.error("LLM API error: %s", e)
        return jsonify(error="The chatbot could not reply just now. Please try again."), 502

    with db() as c:
        ts = now_iso()
        c.executemany(
            "INSERT INTO messages(participant_id, session_id, role, content, phase, created_at) "
            "VALUES(?,?,?,?,?,?)",
            [
                (participant["id"], session_id, "user", text, phase, ts),
                (participant["id"], session_id, "assistant", answer, phase, now_iso()),
            ],
        )
    return jsonify(reply=answer)


# ---------------------------------------------------------------- admin routes

ADMIN_PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Study admin</title>
<style>
 body{{font:16px/1.5 system-ui,sans-serif;max-width:560px;margin:2rem auto;padding:0 1rem}}
 button{{font:inherit;padding:.5rem .9rem;margin:.2rem .2rem .2rem 0;cursor:pointer}}
 .box{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}}
</style>
<h1>Study admin</h1>
<div class="box">
 <p><b>Participants:</b> {total} (attachment {a}, control {b})<br>
 <b>Messages logged:</b> {msgs}</p>
</div>
<div class="box">
 <p><b>Current phase:</b> {phase}</p>
 <p>The phase applies to every participant at once.<br>
 <b>updated</b> changes the persona and wipes memory. <b>retired</b> shuts the chatbot down.</p>
 <form method="post" action="/admin/phase">
  <input type="hidden" name="key" value="{key}">
  <button name="phase" value="normal">Normal</button>
  <button name="phase" value="updated">Apply update</button>
  <button name="phase" value="retired">Retire chatbot</button>
 </form>
</div>
<div class="box">
 <a href="/admin/export.csv?key={key}">Download all messages (CSV)</a>
</div>
"""


@app.get("/admin")
def admin():
    key = request.args.get("key", "")
    if not admin_ok(key):
        return Response("Not authorised", 401)
    with db() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM participants").fetchone()["n"]
        a = c.execute("SELECT COUNT(*) AS n FROM participants WHERE condition='attachment'").fetchone()["n"]
        msgs = c.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        phase = get_setting(c, "phase", "normal")
    return ADMIN_PAGE.format(total=total, a=a, b=total - a, msgs=msgs, phase=phase, key=key)


@app.post("/admin/phase")
def admin_phase():
    if not admin_ok(request.form.get("key", "")):
        return Response("Not authorised", 401)
    phase = request.form.get("phase", "")
    if phase not in ("normal", "updated", "retired"):
        return Response("Bad phase", 400)
    with db() as c:
        if phase != get_setting(c, "phase", "normal"):
            set_setting(c, "phase", phase)
            # Moving the cut-off wipes what the chatbot remembers, but nothing is deleted.
            set_setting(c, "phase_changed_at", now_iso())
            c.execute("INSERT INTO events(phase, created_at) VALUES(?,?)", (phase, now_iso()))
    return Response(status=303, headers={"Location": f"/admin?key={request.form['key']}"})


@app.get("/admin/export.csv")
def admin_export():
    if not admin_ok(request.args.get("key", "")):
        return Response("Not authorised", 401)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["participant", "condition", "session_id", "phase", "role", "content", "created_at_utc"])
    with db() as c:
        rows = c.execute(
            "SELECT m.participant_id, p.condition, m.session_id, m.phase, m.role, m.content, m.created_at "
            "FROM messages m JOIN participants p ON p.id = m.participant_id ORDER BY m.id"
        ).fetchall()
        for r in rows:
            w.writerow(list(r))
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=study_messages.csv"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
