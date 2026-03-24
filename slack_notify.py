import json
import urllib.request
from db import get_config

def send_slack_alert(kit_name, reporter_name, reporter_slack, new_missing, old_missing, last_borrowers):
    """fire off a slack webhook if enabled and missing count went up"""
    enabled = get_config("slack_enabled")
    webhook = get_config("slack_webhook_url")

    if enabled != "true" or not webhook:
        return False

    # build the mention for the reporter
    reporter_tag = f"<@{reporter_slack}>" if reporter_slack else reporter_name

    # build mentions for last 3 borrowers
    borrower_tags = []
    for b in last_borrowers:
        if b.get("slack_id"):
            borrower_tags.append(f"<@{b['slack_id']}>")
        else:
            borrower_tags.append(b["name"])

    borrower_str = ", ".join(borrower_tags) if borrower_tags else "no previous borrowers"

    diff = new_missing - old_missing
    text = (
        f":warning: *Missing bits increased on {kit_name}*\n"
        f"Reported by {reporter_tag} — went from {old_missing} to {new_missing} missing (+{diff})\n"
        f"Previous 3 borrowers: {borrower_str}"
    )

    payload = json.dumps({"text": text}).encode("utf-8")

    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        # dont crash if slack is down or whatever
        print(f"slack webhook failed: {e}")
        return False
