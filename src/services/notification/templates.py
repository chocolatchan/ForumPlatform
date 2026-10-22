"""Notification email templates and HTML rendering helpers.

Ensures strict HTML escaping of untrusted user input to prevent injection attacks.
"""

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    body_text: str
    body_html: str
    category: str  # "mandatory_security" or "optional_activity"


def render_welcome_email(username: str) -> RenderedEmail:
    safe_username = html.escape(username)
    subject = "Welcome to ForumPlatform!"
    body_text = f"Hello {username},\n\nYour account has been registered and verified successfully.\n\nWelcome aboard!\nThe ForumPlatform Team"
    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: sans-serif; color: #333; line-height: 1.5; padding: 20px;">
  <h2 style="color: #2b6cb0;">Welcome to ForumPlatform!</h2>
  <p>Hello <strong>{safe_username}</strong>,</p>
  <p>Your account has been registered and verified successfully.</p>
  <p>You can now participate in discussions, ask questions, and share insights.</p>
  <br>
  <p style="color: #718096; font-size: 0.9em;">— The ForumPlatform Team</p>
</body>
</html>"""
    return RenderedEmail(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        category="mandatory_security",
    )


def render_moderation_email(
    action: str,
    target_type: str,
    reason: str,
) -> RenderedEmail:
    safe_action = html.escape(action)
    safe_target = html.escape(target_type)
    safe_reason = html.escape(reason)

    action_titles = {
        "ban": "Important: Your account has been suspended",
        "unban": "Notice: Your account suspension has been lifted",
        "role_change": "Notice: Your account permissions have changed",
        "soft_delete": f"Notice: Your {target_type} was removed by a moderator",
        "lock": "Notice: Your thread was locked by a moderator",
        "pin": "Notice: Your thread was pinned by a moderator",
    }
    subject = action_titles.get(action, f"Notice regarding your account: {action}")

    body_text = (
        f"Notice from ForumPlatform Moderation\n\n"
        f"Action: {action}\n"
        f"Target: {target_type}\n"
        f"Reason: {reason}\n\n"
        f"If you believe this was done in error, please contact community moderators.\n\n"
        f"— The ForumPlatform Moderation Team"
    )

    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: sans-serif; color: #333; line-height: 1.5; padding: 20px;">
  <h2 style="color: #c53030;">Moderation Notice</h2>
  <p>A moderation action has been applied to your {safe_target}:</p>
  <ul>
    <li><strong>Action:</strong> {safe_action}</li>
    <li><strong>Reason:</strong> {safe_reason}</li>
  </ul>
  <p>If you believe this was done in error, please reach out to our team.</p>
  <br>
  <p style="color: #718096; font-size: 0.9em;">— The ForumPlatform Moderation Team</p>
</body>
</html>"""

    return RenderedEmail(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        category="mandatory_security",
    )


def render_comment_notification_email(
    title: str,
    body_preview: str,
) -> RenderedEmail:
    safe_title = html.escape(title)
    safe_body = html.escape(body_preview)
    subject = f"Discussion Notification: {title}"

    body_text = f"Hello,\n\nYou have a new notification on your discussion:\n\n{title}\n\nPreview:\n{body_preview}\n\nVisit ForumPlatform to reply.\n\n— The ForumPlatform Team"
    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: sans-serif; color: #333; line-height: 1.5; padding: 20px;">
  <h3 style="color: #2b6cb0;">{safe_title}</h3>
  <p style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 12px; margin: 15px 0;">
    {safe_body}
  </p>
  <p>Visit ForumPlatform to view the full discussion and reply.</p>
  <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
  <p style="color: #a0aec0; font-size: 0.8em;">You are receiving this email because you opted into email notifications for thread activities. You can update your preferences in your account settings.</p>
</body>
</html>"""

    return RenderedEmail(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        category="optional_activity",
    )


def render_best_answer_email() -> RenderedEmail:
    subject = "Your answer was accepted as the Best Answer!"
    body_text = "Congratulations!\n\nThe thread author marked your answer as the best answer.\n\nThank you for contributing to the community!\n— The ForumPlatform Team"
    body_html = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: sans-serif; color: #333; line-height: 1.5; padding: 20px;">
  <h2 style="color: #2f855a;">Congratulations!</h2>
  <p>The thread author marked your answer as the <strong>Best Answer</strong>.</p>
  <p>Thank you for contributing your knowledge to the community!</p>
  <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
  <p style="color: #a0aec0; font-size: 0.8em;">You are receiving this email because you opted into email notifications for thread activities.</p>
</body>
</html>"""

    return RenderedEmail(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        category="optional_activity",
    )
