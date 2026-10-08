# Companion chatbot study site

A website where participants chat with Claude. Claude is called from the server, so your API key is never exposed.

## What it does
- Consent page, then random assignment to **attachment** (warm persona "Mira", remembers across sessions) or **control** (neutral assistant, no memory across sessions). Groups stay balanced.
- Each participant gets a short code to return with.
- **Admin page** (`/admin?key=YOUR_ADMIN_KEY`): switch the whole study to
  - **Apply update**: persona changes to "Assistant v2" and memory is wiped (your disruption condition)
  - **Retire chatbot**: shutdown message, chat disabled
  - **Normal**: back to the start
  - Download every message as CSV. Nothing is ever deleted when you change phase.

## Run it on your computer first
```
pip install -r requirements.txt
set ANTHROPIC_API_KEY=your-key        (Windows: use setx, then reopen the terminal)
set ADMIN_KEY=choose-a-long-password
python app.py
```
Open http://localhost:5000 and http://localhost:5000/admin?key=choose-a-long-password

## Put it online (Render, simplest route)
1. Create a free GitHub account and upload this folder as a repository. Do not upload any key.
2. On render.com, choose **New > Web Service** and connect the repository.
3. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn app:app --workers 1 --threads 4 --timeout 60`
4. Under **Environment**, add `ANTHROPIC_API_KEY`, `ADMIN_KEY`, and optionally `CLAUDE_MODEL` and `DAILY_MESSAGE_LIMIT`.
5. Deploy. Render gives you a public link to share with participants.

## Important before real participants use it
- **Data can disappear on free hosting.** Render's free tier wipes the database when the service restarts. Either add a paid persistent disk and set `DB_PATH=/var/data/study.db`, or download the CSV from the admin page every day.
- **Cost.** The Anthropic API is pay-as-you-go. Set a monthly spending limit in the Anthropic Console. `CLAUDE_MODEL=claude-haiku-5-5` is cheaper. Keep the same model for the whole study and report it in your methods.
- **Ethics.** Fill in the bracketed placeholders in `templates/index.html` (your name, university, email, ethics reference, support service). Get ethics approval before recruiting, and prepare a debrief message for the end of the study.
- **Data protection.** Messages are stored as plain text. Tell participants not to share identifying details, and handle the CSV according to your university's rules.
