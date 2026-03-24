import os
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path

for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

key = os.environ.get("GOOGLE_API_KEY", "")
print(f"API key present: {bool(key)} | length: {len(key)}")

import google.generativeai as genai
genai.configure(api_key=key)
model = genai.GenerativeModel("gemini-2.5-flash")
resp = model.generate_content("Reply with exactly: Gemini OK")
print("Response:", resp.text.strip())
meta = resp.usage_metadata
print(f"Tokens — in: {meta.prompt_token_count}, out: {meta.candidates_token_count}")
print("PASS")
