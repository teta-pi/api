import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

# Until tetapi.dev is verified in Resend, only onboarding@resend.dev is allowed as sender.
# After domain verification switch to: "TETA+PI <hello@tetapi.dev>"
FROM_ADDRESS = "TETA+PI <onboarding@resend.dev>"

CLAIM_CONFIRMATION_HTML = """\
<div style="font-family:'Segoe UI',system-ui,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#1A1035">
  <div style="font-size:26px;font-weight:700;margin-bottom:4px">
    <span style="color:#5B45C9">&Theta;</span><span style="font-weight:300">+</span><span style="color:#F59A2E">&pi;</span>
  </div>
  <h1 style="font-size:22px;font-weight:700;margin:24px 0 8px">You're in the registry queue 🎉</h1>
  <p style="font-size:15px;line-height:1.6;color:#4A3F6B;margin:0 0 20px">
    Your claim is registered. You are <strong>position #{position}</strong> on the TETA+PI waitlist.
  </p>
  {founding_block}
  <p style="font-size:15px;line-height:1.6;color:#4A3F6B;margin:0 0 20px">
    What happens next: we verify entities in order. When it's your turn, you'll get a link
    to complete verification — registry check, C2PA signing credentials, Bitcoin timestamp.
  </p>
  <p style="font-size:13px;line-height:1.6;color:#9088B0;margin:28px 0 0">
    TetaPi GmbH · Frankfurt am Main · <a href="https://tetapi.dev" style="color:#5B45C9">tetapi.dev</a>
  </p>
</div>
"""

FOUNDING_BLOCK_HTML = """\
<div style="background:#F4F0FB;border:1px solid #E2DCF0;border-radius:12px;padding:16px 20px;margin:0 0 20px">
  <p style="font-size:14px;line-height:1.5;color:#3A2C5C;margin:0">
    🏅 <strong>Founding member:</strong> your $21 founding price is locked.
    It applies when billing launches — no matter what the price is by then.
  </p>
</div>
"""


async def send_claim_confirmation(email: str, position: int, ready_to_pay: bool) -> None:
    """Send waitlist confirmation via Resend. Failures are logged, never raised."""
    if not settings.resend_api_key:
        logger.info("RESEND_API_KEY not set — skipping confirmation email to %s", email)
        return

    html = CLAIM_CONFIRMATION_HTML.format(
        position=position,
        founding_block=FOUNDING_BLOCK_HTML if ready_to_pay else "",
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": FROM_ADDRESS,
                    "to": [email],
                    "subject": f"TETA+PI — you're #{position} in the registry queue",
                    "html": html,
                },
            )
            if resp.status_code >= 400:
                logger.error("Resend error %s for %s: %s", resp.status_code, email, resp.text)
    except httpx.HTTPError:
        logger.exception("Failed to send confirmation email to %s", email)
