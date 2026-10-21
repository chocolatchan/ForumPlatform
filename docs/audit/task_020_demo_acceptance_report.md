# Task 020: Local Demonstration Procedure & Acceptance Report

## 1. Executive Summary

This document specifies the step-by-step procedure for conducting an end-to-end, live interactive demonstration of the Forum Platform on a developer workstation.

The demonstration uses only standard local tools (`docker compose`, a web browser such as Firefox/Chrome, and `curl`). It exercises real user registration, live Mailpit OTP verification, session cookies, discussion thread publication, role-based navigation, and graceful failure handling.

---

## 2. Prerequisites & Service Startup

### 1. Verify Environment Variables
Ensure `.env` contains valid local test configuration:
```bash
cp -n .env.example .env
```

### 2. Start Full Platform Containers
From the repository root, start all 10 containers in detached mode:
```bash
docker compose up -d
```

### 3. Verify Container Health
Run:
```bash
docker compose ps
```
Confirm that all containers report `healthy` or `running`:
- `forum_api_gateway` on `http://localhost:8000`
- `forum_mailpit` on `http://localhost:8025` (UI) and `1025` (SMTP)
- `forum_postgres` (with logical DBs for each service)
- `forum_redis` (session cache)
- Microservices: `forum_user_service`, `forum_content_service`, `forum_interaction_service`, `forum_moderation_service`, `forum_notification_service`, `forum_search_service`.

---

## 3. End-to-End Walkthrough Demonstration Script

### Step 1: Access Public Discussions
1. Open your browser and navigate to:
   ```
   http://localhost:8000/
   ```
2. **Observe:**
   - The Single Page Application loads with header "ForumPlatform".
   - Unauthenticated state: The top navigation bar displays `Discussions`, `Log In`, and `Sign Up`.
   - The `+ New Thread` button is hidden from guests.
   - Public community discussions load from the Content Service.

---

### Step 2: Register a New User & Inspect Neutral Notification
1. Click **Sign Up** in the navigation bar.
2. Fill out the registration form:
   - **Username:** `demo_tester`
   - **Email:** `demo_tester@example.test`
   - **Password:** `DemoPassword2026!`
3. Click **Submit Registration**.
4. **Observe:**
   - The green alert banner appears: *"A verification code has been sent if the email is eligible. Please check your inbox."*
   - The SPA smoothly switches to the **Verify OTP** view, with the email field prefilled.

---

### Step 3: Retrieve Live OTP from Mailpit & Activate Account
1. In a new browser tab, navigate to the Mailpit web interface:
   ```
   http://localhost:8025/
   ```
2. **Observe:**
   - A new email appears with Subject: `Your Forum Platform Verification Code`.
   - To: `demo_tester@example.test`.
   - Read the 6-digit OTP code in the email message body.
3. Switch back to the Forum Platform tab (`http://localhost:8000`).
4. Enter the 6-digit code in the OTP field and click **Verify Account**.
5. **Observe:**
   - Success banner appears: *"Account activated successfully! You can now log in."*
   - The view automatically transitions to the **Log In** screen.

---

### Step 4: Authenticate & Verify Role-Aware Ingress
1. On the Log In screen, enter:
   - **Identifier:** `demo_tester`
   - **Password:** `DemoPassword2026!`
2. Click **Log In**.
3. **Observe:**
   - Banner: *"Welcome back, demo_tester!"*
   - Navigation bar updates dynamically:
     - Displays `member` badge.
     - Displays `Profile (demo_tester)` button.
     - Displays `Log Out` button.
     - Hides `Log In` and `Sign Up`.
   - The **+ New Thread** button is now visible in the Community Discussions header.
4. Open browser Developer Tools (`F12`) -> Application/Storage -> Cookies:
   - Verify `forum_session` exists with `HttpOnly` checked, `SameSite=Lax`, and `Path=/`.
   - Inspect `localStorage` and `sessionStorage`: Both are empty (zero tokens stored in JS memory).

---

### Step 5: Publish a Discussion Thread
1. Click **+ New Thread**.
2. Select a category (e.g., *General Discussion* or *Python Development*).
3. Enter:
   - **Title:** `Interactive Demo: Microservice Orchestration Verification`
   - **Content:** `This thread was created live through the SPA to demonstrate transactional outbox publishing and gateway routing.`
4. Click **Publish Thread**.
5. **Observe:**
   - Success alert: *"Thread published successfully!"*
   - Home view renders with the new thread listed at the top.

---

### Step 6: Test Role Protection & Guarded Routing
1. Open browser Developer Tools Console (`F12` -> Console).
2. Attempt to trigger the administrative route as a standard member:
   ```javascript
   window.navigateTo('view-admin');
   ```
3. **Observe:**
   - The router intercepts unauthorized navigation.
   - Redirects back to Home feed.
   - Shows red error banner: *"Access Denied: Administrative and moderator privileges required."*

---

### Step 7: Log Out & Unauthenticated State Return
1. In the navigation bar, click **Log Out**.
2. **Observe:**
   - Info alert: *"You have been logged out."*
   - Top navigation returns to guest layout (`Log In` / `Sign Up`).
   - `+ New Thread` button disappears.
   - DevTools Cookies shows `forum_session` cookie has expired or been purged.

---

## 4. Downstream Outage Resilience Demonstration

To demonstrate system resilience when a downstream microservice is temporarily stopped:
1. In your terminal, stop the Search Service:
   ```bash
   docker compose stop forum_search_service
   ```
2. Navigate to `http://localhost:8000/`.
3. Notice core discussion listings still render normally (provided by Content Service).
4. Perform an action dependent on search or trigger a simulated gateway 503 check.
5. The API Gateway emits canonical 503 error payloads (`SERVICE_UNAVAILABLE`) with `request_id` tracking without crashing the front-end.
6. Restart the service:
   ```bash
   docker compose start forum_search_service
   ```
