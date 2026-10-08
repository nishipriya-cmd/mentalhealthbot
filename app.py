"""
Web chatbot for the Human-AI attachment and disruption study.

The browser never sees your API key. All calls to the AI happen here, on the server.

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
        _client =
