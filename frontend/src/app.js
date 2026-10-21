import { ApiClient } from "./api.js";
import { AuthManager } from "./auth.js";

// Initialize API client & auth manager
export const api = new ApiClient("/api/v1");
export const auth = new AuthManager(api);

// Global DOM elements
const alertBox = document.getElementById("global-alert");
const navContainer = document.getElementById("navbar");
const views = document.querySelectorAll(".view");

let alertTimeout = null;

// Helper: Show notification banner
export function showAlert(type, message, durationMs = 5000) {
  if (!alertBox) return;
  if (alertTimeout) {
    clearTimeout(alertTimeout);
    alertTimeout = null;
  }
  alertBox.className = `alert ${type}`;
  alertBox.textContent = message;
  alertBox.style.display = "block";

  if (durationMs > 0) {
    alertTimeout = setTimeout(() => {
      alertBox.style.display = "none";
      alertTimeout = null;
    }, durationMs);
  }
}

export function hideAlert() {
  if (alertTimeout) {
    clearTimeout(alertTimeout);
    alertTimeout = null;
  }
  if (alertBox) alertBox.style.display = "none";
}

// Router: Show specific view by element ID
export function navigateTo(viewId, preserveAlert = false) {
  // Role-aware view guards
  if (viewId === "view-admin" && !auth.isModerator()) {
    navigateTo("view-home");
    showAlert("error", "Access Denied: Administrative and moderator privileges required.");
    return;
  }

  if (!preserveAlert) {
    hideAlert();
  }
  views.forEach((v) => {
    if (v.id === viewId) {
      v.classList.add("active");
    } else {
      v.classList.remove("active");
    }
  });

  // Load view-specific data
  if (viewId === "view-home") {
    loadThreads();
  } else if (viewId === "view-profile") {
    loadProfileDetails();
  } else if (viewId === "view-create-thread") {
    loadCategoriesForThreadCreation();
  } else if (viewId === "view-admin") {
    loadAdminDashboard();
  }
}

// Update Top Navigation Bar according to Role
function updateNavigation(user) {
  if (!navContainer) return;
  navContainer.innerHTML = "";

  const homeBtn = document.createElement("button");
  homeBtn.className = "nav-btn";
  homeBtn.textContent = "Discussions";
  homeBtn.onclick = () => navigateTo("view-home");
  navContainer.appendChild(homeBtn);

  const createThreadBtn = document.getElementById("btn-create-thread-view");

  if (!user) {
    // Guest navigation
    if (createThreadBtn) createThreadBtn.style.display = "none";

    const loginBtn = document.createElement("button");
    loginBtn.className = "nav-btn";
    loginBtn.textContent = "Log In";
    loginBtn.onclick = () => navigateTo("view-login");
    navContainer.appendChild(loginBtn);

    const registerBtn = document.createElement("button");
    registerBtn.className = "nav-btn primary";
    registerBtn.textContent = "Sign Up";
    registerBtn.onclick = () => navigateTo("view-register");
    navContainer.appendChild(registerBtn);
  } else {
    // Authenticated navigation
    if (createThreadBtn) createThreadBtn.style.display = "block";

    // Role-aware badge
    const badge = document.createElement("span");
    badge.className = `badge badge-${user.role}`;
    badge.textContent = user.role;
    navContainer.appendChild(badge);

    // Moderator / Admin View Link
    if (auth.isModerator()) {
      const adminBtn = document.createElement("button");
      adminBtn.className = "nav-btn";
      adminBtn.textContent = user.role === "admin" ? "Admin Panel" : "Moderation Queue";
      adminBtn.onclick = () => navigateTo("view-admin");
      navContainer.appendChild(adminBtn);
    }

    const profileBtn = document.createElement("button");
    profileBtn.className = "nav-btn";
    profileBtn.textContent = `Profile (${user.username})`;
    profileBtn.onclick = () => navigateTo("view-profile");
    navContainer.appendChild(profileBtn);

    const logoutBtn = document.createElement("button");
    logoutBtn.className = "nav-btn";
    logoutBtn.textContent = "Log Out";
    logoutBtn.onclick = async () => {
      await auth.logout();
      showAlert("info", "You have been logged out.");
      navigateTo("view-home", true);
    };
    navContainer.appendChild(logoutBtn);
  }
}

// -------------------------------------------------------------
// View Handlers & Logic
// -------------------------------------------------------------

// 1. Thread Listing
async function loadThreads() {
  const container = document.getElementById("thread-container");
  if (!container) return;

  const res = await api.getThreads();
  if (!res.ok) {
    container.innerHTML = `<p class="subtitle" style="color: var(--danger)">Error loading threads: ${escapeHtml(res.error.message)}</p>`;
    return;
  }

  const items = res.data?.items || [];
  if (items.length === 0) {
    container.innerHTML = `<p class="subtitle" style="text-align: center; padding: 2rem;">No discussions found. Be the first to start a topic!</p>`;
    return;
  }

  container.innerHTML = items
    .map(
      (t) => `
      <div class="thread-item">
        <div class="thread-title">${escapeHtml(t.title)}</div>
        <div class="thread-meta">
          <span>By: ${escapeHtml(t.author_username || "Anonymous")}</span>
          <span>Category: ${escapeHtml(t.category_name || "General")}</span>
          <span>Views: ${t.views_count ?? 0}</span>
          <span>Replies: ${t.replies_count ?? 0}</span>
        </div>
      </div>
    `
    )
    .join("");
}

// 2. Registration Flow
function setupRegistration() {
  const form = document.getElementById("form-register");
  if (!form) return;

  form.onsubmit = async (e) => {
    e.preventDefault();
    const username = document.getElementById("reg-username").value.trim();
    const email = document.getElementById("reg-email").value.trim();
    const password = document.getElementById("reg-password").value;

    const res = await api.register(username, email, password);
    if (res.ok) {
      // Neutral message displayed without leaking account existence
      showAlert("success", res.data.message || "Registration accepted! Please check your email for the verification code.");
      document.getElementById("verify-email").value = email;
      navigateTo("view-verify-otp", true);
    } else {
      showAlert("error", `Registration failed: ${res.error.message}`);
    }
  };

  const gotoLogin = document.getElementById("btn-goto-login");
  if (gotoLogin) gotoLogin.onclick = () => navigateTo("view-login");
}

// 3. OTP Verification Flow
function setupOtpVerification() {
  const form = document.getElementById("form-verify-otp");
  if (!form) return;

  form.onsubmit = async (e) => {
    e.preventDefault();
    const email = document.getElementById("verify-email").value.trim();
    const otp = document.getElementById("verify-otp-code").value.trim();

    const res = await api.verifyOtp(email, otp);
    if (res.ok) {
      showAlert("success", "Account activated successfully! You can now log in.");
      navigateTo("view-login", true);
    } else {
      showAlert("error", `Verification failed: ${res.error.message}`);
    }
  };

  const resendBtn = document.getElementById("btn-resend-otp");
  if (resendBtn) {
    resendBtn.onclick = async () => {
      const email = document.getElementById("verify-email").value.trim();
      if (!email) {
        showAlert("error", "Email is required to resend OTP.");
        return;
      }
      const res = await api.resendOtp(email);
      if (res.ok) {
        showAlert("info", res.data.message || "A new code has been sent if eligible.");
      } else {
        showAlert("error", `Failed to resend code: ${res.error.message}`);
      }
    };
  }
}

// 4. Login Flow
function setupLogin() {
  const form = document.getElementById("form-login");
  if (!form) return;

  form.onsubmit = async (e) => {
    e.preventDefault();
    const identifier = document.getElementById("login-identifier").value.trim();
    const password = document.getElementById("login-password").value;

    const res = await api.login(identifier, password);
    if (res.ok && res.data?.user) {
      auth.setAuthenticated(res.data.user, res.data.session);
      showAlert("success", `Welcome back, ${res.data.user.username}!`);
      navigateTo("view-home", true);
    } else {
      showAlert("error", `Login failed: ${res.error.message}`);
    }
  };

  const gotoReg = document.getElementById("btn-goto-register");
  if (gotoReg) gotoReg.onclick = () => navigateTo("view-register");
}

// 5. Profile Details
async function loadProfileDetails() {
  const container = document.getElementById("profile-details");
  if (!container) return;

  const res = await api.getProfile();
  if (res.ok && res.data) {
    const u = res.data;
    container.innerHTML = `
      <div><strong>Username:</strong> ${escapeHtml(u.username)}</div>
      <div><strong>Email:</strong> ${escapeHtml(u.email || "Private")}</div>
      <div><strong>Role:</strong> <span class="badge badge-${escapeHtml(u.role)}">${escapeHtml(u.role)}</span></div>
      <div><strong>Display Name:</strong> ${escapeHtml(u.display_name || "Not set")}</div>
      <div><strong>Bio:</strong> ${escapeHtml(u.bio || "None")}</div>
      <div><strong>Member Since:</strong> ${u.created_at ? new Date(u.created_at).toLocaleDateString() : "N/A"}</div>
    `;
  } else {
    container.innerHTML = `<p class="subtitle" style="color: var(--danger)">Unable to load profile.</p>`;
  }
}

// 6. Thread Creation Flow
async function loadCategoriesForThreadCreation() {
  const select = document.getElementById("thread-category");
  if (!select) return;

  const res = await api.getCategories();
  if (res.ok && res.data?.items) {
    select.innerHTML = res.data.items
      .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`)
      .join("");
  }
}

function setupCreateThread() {
  const form = document.getElementById("form-create-thread");
  if (!form) return;

  form.onsubmit = async (e) => {
    e.preventDefault();
    const title = document.getElementById("thread-title").value.trim();
    const categoryId = document.getElementById("thread-category").value;
    const content = document.getElementById("thread-content").value.trim();

    const res = await api.createThread(title, content, categoryId);
    if (res.ok) {
      showAlert("success", "Thread published successfully!");
      navigateTo("view-home", true);
    } else {
      showAlert("error", `Failed to publish thread: ${res.error.message}`);
    }
  };

  const cancelBtn = document.getElementById("btn-cancel-create-thread");
  if (cancelBtn) cancelBtn.onclick = () => navigateTo("view-home");

  const newThreadBtn = document.getElementById("btn-create-thread-view");
  if (newThreadBtn) newThreadBtn.onclick = () => navigateTo("view-create-thread");
}

// 7. Admin / Moderator Dashboard
async function loadAdminDashboard() {
  const userSection = document.getElementById("admin-user-section");
  const userTbody = document.getElementById("admin-user-tbody");
  const modTbody = document.getElementById("moderation-reports-tbody");

  // Admin user management table (Admin only)
  if (userSection) {
    if (auth.isAdmin()) {
      userSection.style.display = "block";
      const uRes = await api.getAdminUsers();
      if (uRes.ok && uRes.data?.items && userTbody) {
        userTbody.innerHTML = uRes.data.items
          .map(
            (u) => `
            <tr>
              <td>${escapeHtml(u.username)}</td>
              <td>${escapeHtml(u.email || "-")}</td>
              <td><span class="badge badge-${escapeHtml(u.role)}">${escapeHtml(u.role)}</span></td>
              <td>${u.is_banned ? '<span style="color: var(--danger)">Banned</span>' : '<span style="color: var(--success)">Active</span>'}</td>
              <td>
                ${
                  u.role === "member"
                    ? `<button class="btn btn-secondary" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="window.promoteUser(encodeURIComponent('${escapeHtml(u.id)}'), 'moderator')">Promote to Mod</button>`
                    : "-"
                }
              </td>
            </tr>
          `
          )
          .join("");
      }
    } else {
      userSection.style.display = "none";
    }
  }

  // Moderation queue (Moderator and Admin)
  if (modTbody) {
    const mRes = await api.getModerationReports();
    if (mRes.ok && mRes.data?.items) {
      const reports = mRes.data.items;
      if (reports.length === 0) {
        modTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted)">No pending reports.</td></tr>`;
      } else {
        modTbody.innerHTML = reports
          .map(
            (r) => `
            <tr>
              <td>${escapeHtml(r.target_type)}</td>
              <td>${escapeHtml(r.reason)}</td>
              <td>${escapeHtml(r.status)}</td>
              <td>${new Date(r.created_at).toLocaleDateString()}</td>
              <td>-</td>
            </tr>
          `
          )
          .join("");
      }
    }
  }
}

// Window helper for role changes
window.promoteUser = async (userId, role) => {
  const res = await api.changeUserRole(userId, role);
  if (res.ok) {
    showAlert("success", `User role updated to ${role}.`);
    loadAdminDashboard();
  } else {
    showAlert("error", `Failed to change role: ${res.error.message}`);
  }
};

// Security Helper: HTML escaping to prevent XSS
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Expose helpers on window for browser automation verification
window.escapeHtml = escapeHtml;
window.navigateTo = navigateTo;
window.auth = auth;

// Initialization on DOMContentLoaded
window.addEventListener("DOMContentLoaded", async () => {
  // Brand logo click
  const logo = document.getElementById("brand-logo");
  if (logo) logo.onclick = () => navigateTo("view-home");

  // Profile logout
  const profileLogout = document.getElementById("btn-profile-logout");
  if (profileLogout) {
    profileLogout.onclick = async () => {
      await auth.logout();
      showAlert("info", "You have been logged out.");
      navigateTo("view-home", true);
    };
  }

  // Subscribe navigation updates to auth changes
  auth.subscribe((user) => {
    updateNavigation(user);
  });

  // Setup form handlers
  setupRegistration();
  setupOtpVerification();
  setupLogin();
  setupCreateThread();

  // Initial session check
  await auth.checkSession();

  // Initial navigation
  navigateTo("view-home");
});

