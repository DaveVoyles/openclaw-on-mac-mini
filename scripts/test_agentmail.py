"""Probe AgentMail API routes to find the correct send endpoint."""
import urllib.request
import json
from urllib.parse import quote

KEY = "am_us_9ad3acb06e390267db4521883e586b1f780d0c3f532767fc075824f5327676dc"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
INBOX_ID = "openclaw-davevoyles@agentmail.to"

send_body = json.dumps({
    "to": "davevoyles@gmail.com",
    "subject": "OpenClaw test",
    "text": "hello from openclaw",
}).encode()

encoded_inbox = quote(INBOX_ID, safe="")
url = f"https://api.agentmail.to/v0/inboxes/{encoded_inbox}/messages/send"
print("URL:", url)

req = urllib.request.Request(url, data=send_body, headers=HEADERS, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        print(f"-> {r.status} {r.read()}")
except urllib.error.HTTPError as e:
    print(f"-> {e.code} {e.read()}")
except Exception as ex:
    print(f"-> ERROR: {ex}")
