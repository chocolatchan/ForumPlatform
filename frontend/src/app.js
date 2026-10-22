import { ApiClient } from "./api.js";
import { AuthManager } from "./auth.js";

// -----------------------------------------------------------------------------
// Initialize API client & auth manager
// -----------------------------------------------------------------------------
export const api = new ApiClient("/api/v1");
export const auth = new AuthManager(api);

// Global DOM elements
const alertBox = document.getElementById("global-alert");
const alertText = document.getElementById("global-alert-text");
const alertCloseBtn = document.getElementById("global-alert-close");
const navContainer = document.getElementById("navbar");
const views = document.querySelectorAll(".view");

let alertTimeout = null;

// Active state tracking
let activeCategoryId = null;
let currentThreadListCursor = null;
let currentActiveThread = null;
let otpCountdownTimer = null;

// -----------------------------------------------------------------------------
// Safe Markdown Parser (Disables raw HTML, escapes untrusted input)
// -----------------------------------------------------------------------------
export function safeMarkdownToHtml(markdownText) {
  if (!markdownText) return "";

  // 1. HTML Escape to prevent any XSS injection
  let safe = markdownText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

  // 2. Fenced code blocks ```code```
  safe = safe.replace(/```([\s\S]*?)```/g, (match, p1) => {
    return `<pre><code>${p1.trim()}</code></pre>`;
  });

  // 3. Inline code `code`
  safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");

  // 4. Blockquotes > quote
  safe = safe.replace(/^>\s?(.*)$/gm, "<blockquote>$1</blockquote>");

  // 5. Bold **text**
  safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // 6. Italic *text*
  safe = safe.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // 7. Links [text](url) - enforce safe protocols (http, https, mailto)
  safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // 8. Paragraphs / line breaks
  safe = safe
    .split(/\n\n+/)
    .map((para) => `<p>${para.replace(/\n/g, "<br/>")}</p>`)
    .join("");

  return safe;
}

// -----------------------------------------------------------------------------
// Notification Helpers
// -----------------------------------------------------------------------------
export function showAlert(type, message, durationMs = 6000) {
  if (!alertBox || !alertText) return;
  if (alertTimeout) {
    clearTimeout(alertTimeout);
    alertTimeout = null;
  }
  alertBox.className = `alert ${type}`;
  alertText.textContent = `[${type.toUpperCase()}] ${message}`;
  alertBox.style.display = "flex";

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

if (alertCloseBtn) {
  alertCloseBtn.onclick = hideAlert;
}

// -----------------------------------------------------------------------------
// Router: Show specific view by element ID
// -----------------------------------------------------------------------------
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
    loadCategories();
    loadThreads();
  } else if (viewId === "view-profile") {
    loadProfileDetails();
  } else if (viewId === "view-create-thread") {
    loadCategoriesForThreadCreation();
  } else if (viewId === "view-admin") {
    loadAdminDashboard();
  }
}

// -----------------------------------------------------------------------------
// Navigation Bar Manager
// -----------------------------------------------------------------------------
function updateNavigation(user) {
  if (!navContainer) return;
  navContainer.innerHTML = "";

  const feedBtn = document.createElement("button");
  feedBtn.className = "btn btn-sm";
  feedBtn.textContent = "DISCUSSIONS";
  feedBtn.onclick = () => navigateTo("view-home");
  navContainer.appendChild(feedBtn);

  const createThreadBtn = document.getElementById("btn-create-thread-view");

  if (!user) {
    if (createThreadBtn) createThreadBtn.style.display = "none";

    const loginBtn = document.createElement("button");
    loginBtn.className = "btn btn-sm";
    loginBtn.textContent = "Log In";
    loginBtn.onclick = () => navigateTo("view-login");
    navContainer.appendChild(loginBtn);

    const registerBtn = document.createElement("button");
    registerBtn.className = "btn btn-sm btn-primary";
    registerBtn.textContent = "Sign Up";
    registerBtn.onclick = () => navigateTo("view-register");
    navContainer.appendChild(registerBtn);
  } else {
    if (createThreadBtn) createThreadBtn.style.display = "inline-flex";

    // Moderator/Admin Panel Link
    if (auth.isModerator()) {
      const adminBtn = document.createElement("button");
      adminBtn.className = "btn btn-sm";
      adminBtn.textContent = auth.isAdmin() ? "OPERATOR // ADMIN" : "MODERATION";
      adminBtn.onclick = () => navigateTo("view-admin");
      navContainer.appendChild(adminBtn);
    }

    // User Profile Pill
    const profileBtn = document.createElement("button");
    profileBtn.className = "btn btn-sm";
    profileBtn.innerHTML = `<span>Profile (${user.username})</span> <span class="badge token token-${user.role}">${user.role}</span>`;
    profileBtn.onclick = () => navigateTo("view-profile");
    navContainer.appendChild(profileBtn);

    // Quick Logout Button
    const logoutBtn = document.createElement("button");
    logoutBtn.className = "btn btn-sm btn-destructive";
    logoutBtn.textContent = "Log Out";
    logoutBtn.onclick = async () => {
      await auth.logout();
      showAlert("info", "Successfully logged out.");
      navigateTo("view-home");
    };
    navContainer.appendChild(logoutBtn);
  }
}

// Subscribe navigation updates to auth state changes
auth.subscribe(updateNavigation);

// -----------------------------------------------------------------------------
// SCREEN 01: Registration & 6-Slot OTP
// -----------------------------------------------------------------------------
const formRegister = document.getElementById("form-register");
const regUsername = document.getElementById("reg-username");
const regEmail = document.getElementById("reg-email");
const regPassword = document.getElementById("reg-password");
const btnToggleRegPw = document.getElementById("btn-toggle-reg-pw");
const btnGotoLogin = document.getElementById("btn-goto-login");

if (btnToggleRegPw) {
  btnToggleRegPw.onclick = () => {
    const isPw = regPassword.type === "password";
    regPassword.type = isPw ? "text" : "password";
    btnToggleRegPw.textContent = isPw ? "HIDE" : "SHOW";
  };
}

if (btnGotoLogin) {
  btnGotoLogin.onclick = () => navigateTo("view-login");
}

if (formRegister) {
  formRegister.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = regUsername.value.trim();
    const email = regEmail.value.trim();
    const password = regPassword.value;

    if (!username || !email || !password) {
      showAlert("error", "Validation failed: All fields are strictly required.");
      return;
    }

    const res = await api.register(username, email, password);
    if (res.ok || res.status === 202) {
      // 202 Accepted anti-enumeration
      showAlert("success", "If the registration details are eligible, a verification code has been sent.", 8000);
      document.getElementById("verify-email").value = email;
      initOtpTimer(600); // 10 minutes authoritative server TTL
      navigateTo("view-verify-otp", true);
    } else {
      showAlert("error", `Registration rejected: ${res.error?.message || "Unknown error"}`);
    }
  });
}

// 6-Slot OTP Input Behaviors
const otpSlots = document.querySelectorAll(".otp-slot");
const hiddenOtpInput = document.getElementById("verify-otp-code");

function updateHiddenOtp() {
  let code = "";
  otpSlots.forEach((slot) => {
    code += slot.value;
  });
  if (hiddenOtpInput) hiddenOtpInput.value = code;
  return code;
}

otpSlots.forEach((slot, idx) => {
  slot.addEventListener("input", (e) => {
    const val = e.target.value.replace(/[^0-9]/g, "");
    slot.value = val ? val[0] : "";
    updateHiddenOtp();
    if (val && idx < otpSlots.length - 1) {
      otpSlots[idx + 1].focus();
    }
  });

  slot.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && !slot.value && idx > 0) {
      otpSlots[idx - 1].focus();
    }
  });

  slot.addEventListener("paste", (e) => {
    e.preventDefault();
    const pasteData = (e.clipboardData || window.clipboardData).getData("text").trim();
    const digits = pasteData.replace(/[^0-9]/g, "");
    for (let i = 0; i < otpSlots.length; i++) {
      otpSlots[i].value = digits[i] || "";
    }
    updateHiddenOtp();
    const nextIdx = Math.min(digits.length, otpSlots.length - 1);
    otpSlots[nextIdx].focus();
  });
});

function initOtpTimer(seconds) {
  if (otpCountdownTimer) clearInterval(otpCountdownTimer);
  let remaining = seconds;
  const timerDisplay = document.getElementById("otp-timer-counter");

  otpCountdownTimer = setInterval(() => {
    remaining--;
    if (timerDisplay) {
      const mins = Math.floor(remaining / 60);
      const secs = remaining % 60;
      timerDisplay.textContent = `EXPIRY: ${mins}M ${secs < 10 ? "0" : ""}${secs}S`;
    }
    if (remaining <= 0) {
      clearInterval(otpCountdownTimer);
      if (timerDisplay) timerDisplay.textContent = "EXPIRED: REQUEST RESEND";
    }
  }, 1000);
}

const formVerifyOtp = document.getElementById("form-verify-otp");
const btnResendOtp = document.getElementById("btn-resend-otp");

if (formVerifyOtp) {
  formVerifyOtp.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("verify-email").value.trim();
    // Read from hiddenOtpInput (also allows direct automation injection)
    const otp = hiddenOtpInput?.value || updateHiddenOtp();

    if (otp.length !== 6) {
      showAlert("error", "Invalid OTP format: Must contain exactly 6 digits.");
      return;
    }

    const res = await api.verifyOtp(email, otp);
    if (res.ok) {
      showAlert("success", "Account activated successfully. Please sign in.");
      formVerifyOtp.reset();
      otpSlots.forEach((s) => (s.value = ""));
      navigateTo("view-login", true);
    } else {
      showAlert("error", `Verification rejected: ${res.error?.message || "Invalid or expired OTP code."}`);
    }
  });
}

if (btnResendOtp) {
  btnResendOtp.addEventListener("click", async () => {
    const email = document.getElementById("verify-email").value.trim();
    if (!email) {
      showAlert("error", "No recipient email found.");
      return;
    }
    const res = await api.resendOtp(email);
    if (res.ok || res.status === 202) {
      showAlert("success", "New OTP code transmitted. Cooldown active (60s).");
      initOtpTimer(600);
    } else {
      showAlert("error", `Resend failed: ${res.error?.message || "Rate limited."}`);
    }
  });
}

// -----------------------------------------------------------------------------
// SCREEN 02: Login & Session
// -----------------------------------------------------------------------------
const formLogin = document.getElementById("form-login");
const loginIdentifier = document.getElementById("login-identifier");
const loginPassword = document.getElementById("login-password");
const btnToggleLoginPw = document.getElementById("btn-toggle-login-pw");
const btnGotoRegister = document.getElementById("btn-goto-register");
const btnGotoRecovery = document.getElementById("btn-goto-recovery");

if (btnGotoRecovery) {
  btnGotoRecovery.onclick = () => {
    formLogin.reset();
    navigateTo("view-recovery-request");
  };
}

if (btnToggleLoginPw) {
  btnToggleLoginPw.onclick = () => {
    const isPw = loginPassword.type === "password";
    loginPassword.type = isPw ? "text" : "password";
    btnToggleLoginPw.textContent = isPw ? "HIDE" : "SHOW";
  };
}

if (btnGotoRegister) {
  btnGotoRegister.onclick = () => navigateTo("view-register");
}

if (formLogin) {
  formLogin.addEventListener("submit", async (e) => {
    e.preventDefault();
    const login = loginIdentifier.value.trim();
    const password = loginPassword.value;

    const res = await api.login(login, password);
    if (res.ok && res.data?.user) {
      auth.setAuthenticated(res.data.user, res.data.session);
      showAlert("success", `Authenticated as ${res.data.user.username} [${res.data.user.role}]`);
      formLogin.reset();
      navigateTo("view-home", true);
    } else {
      showAlert("error", `Authentication failed: ${res.error?.message || "Invalid credentials."}`);
    }
  });
}

// -----------------------------------------------------------------------------
// SCREEN 02B: Password Recovery Lifecycle (Task 029)
// -----------------------------------------------------------------------------
let recoveryState = {
  email: "",
  resetToken: null,
};

function clearRecoveryState() {
  recoveryState.email = "";
  recoveryState.resetToken = null;
  const reqForm = document.getElementById("form-recovery-request");
  if (reqForm) reqForm.reset();
  const vForm = document.getElementById("form-recovery-verify");
  if (vForm) vForm.reset();
  const rForm = document.getElementById("form-recovery-reset");
  if (rForm) rForm.reset();
  const errDiv = document.getElementById("recovery-reset-error");
  if (errDiv) {
    errDiv.style.display = "none";
    errDiv.textContent = "";
  }
}

// 1. Request form
const formRecoveryRequest = document.getElementById("form-recovery-request");
const recoveryEmail = document.getElementById("recovery-email");
const btnRecoveryBackToLogin = document.getElementById("btn-recovery-back-to-login");

if (btnRecoveryBackToLogin) {
  btnRecoveryBackToLogin.onclick = () => {
    clearRecoveryState();
    navigateTo("view-login");
  };
}

if (formRecoveryRequest) {
  formRecoveryRequest.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = recoveryEmail.value.trim();
    if (!email) return;

    recoveryState.email = email;
    const res = await api.requestRecovery(email);
    // Neutral 202 is always returned per DEC-001 anti-enumeration contract
    showAlert("success", res.data?.message || "Recovery code sent if account exists.");
    const targetEmailDiv = document.getElementById("recovery-verify-target-email");
    if (targetEmailDiv) targetEmailDiv.textContent = email;
    navigateTo("view-recovery-verify");
  });
}

// 2. Verify form
const formRecoveryVerify = document.getElementById("form-recovery-verify");
const recoveryOtp = document.getElementById("recovery-otp");
const btnRecoveryVerifyCancel = document.getElementById("btn-recovery-verify-cancel");

if (btnRecoveryVerifyCancel) {
  btnRecoveryVerifyCancel.onclick = () => {
    clearRecoveryState();
    navigateTo("view-login");
  };
}

if (formRecoveryVerify) {
  formRecoveryVerify.addEventListener("submit", async (e) => {
    e.preventDefault();
    const otp = recoveryOtp.value.trim();
    if (!otp) return;

    const res = await api.verifyRecovery(recoveryState.email, otp);
    if (res.ok && res.data?.reset_token) {
      recoveryState.resetToken = res.data.reset_token;
      showAlert("success", "Recovery code verified. Please set your new password.");
      formRecoveryVerify.reset();
      navigateTo("view-recovery-reset");
    } else {
      showAlert("error", res.error?.message || "Invalid or expired recovery code.");
    }
  });
}

// 3. Reset form
const formRecoveryReset = document.getElementById("form-recovery-reset");
const recoveryNewPassword = document.getElementById("recovery-new-password");
const recoveryConfirmPassword = document.getElementById("recovery-confirm-password");
const btnToggleRecoveryPw = document.getElementById("btn-toggle-recovery-pw");
const recoveryResetError = document.getElementById("recovery-reset-error");
const btnRecoveryResetCancel = document.getElementById("btn-recovery-reset-cancel");

if (btnToggleRecoveryPw) {
  btnToggleRecoveryPw.onclick = () => {
    const isPw = recoveryNewPassword.type === "password";
    recoveryNewPassword.type = isPw ? "text" : "password";
    btnToggleRecoveryPw.textContent = isPw ? "HIDE" : "SHOW";
  };
}

if (btnRecoveryResetCancel) {
  btnRecoveryResetCancel.onclick = () => {
    clearRecoveryState();
    navigateTo("view-login");
  };
}

if (formRecoveryReset) {
  formRecoveryReset.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (recoveryResetError) {
      recoveryResetError.style.display = "none";
      recoveryResetError.textContent = "";
    }

    const newPwd = recoveryNewPassword.value;
    const confirmPwd = recoveryConfirmPassword.value;

    if (newPwd !== confirmPwd) {
      if (recoveryResetError) {
        recoveryResetError.textContent = "New password and confirmation do not match.";
        recoveryResetError.style.display = "block";
      }
      return;
    }

    if (!recoveryState.resetToken) {
      showAlert("error", "Reset session expired. Please restart password recovery.");
      clearRecoveryState();
      navigateTo("view-recovery-request");
      return;
    }

    const res = await api.resetPassword(recoveryState.resetToken, newPwd);
    if (res.ok) {
      showAlert("success", "Password successfully reset! Please sign in with your new password.");
      clearRecoveryState();
      navigateTo("view-login");
    } else {
      const msg = res.error?.message || "Failed to reset password.";
      if (recoveryResetError) {
        recoveryResetError.textContent = msg;
        recoveryResetError.style.display = "block";
      } else {
        showAlert("error", msg);
      }
    }
  });
}

// -----------------------------------------------------------------------------
// SCREEN 03: Feed, Categories & Search
// -----------------------------------------------------------------------------
const categoryFilterList = document.getElementById("category-filter-list");
const threadContainer = document.getElementById("thread-container");
const feedSearchInput = document.getElementById("feed-search-input");
const feedPagination = document.getElementById("feed-pagination");
const paginationCursorVal = document.getElementById("pagination-cursor-val");
const btnPaginateNext = document.getElementById("btn-paginate-next");
const btnCreateThreadView = document.getElementById("btn-create-thread-view");

if (btnCreateThreadView) {
  btnCreateThreadView.onclick = () => navigateTo("view-create-thread");
}

async function loadCategories() {
  if (!categoryFilterList) return;
  const res = await api.getCategories();
  if (!res.ok || !res.data) return;

  const categories = res.data.items || res.data || [];
  categoryFilterList.innerHTML = `<div class="category-filter-item ${!activeCategoryId ? "active" : ""}" data-id="">ALL CHANNELS</div>`;

  categories.forEach((cat) => {
    const item = document.createElement("div");
    item.className = `category-filter-item ${activeCategoryId === cat.id ? "active" : ""}`;
    item.dataset.id = cat.id;
    item.textContent = `# ${cat.name.toUpperCase()}`;
    item.onclick = () => {
      activeCategoryId = cat.id;
      loadCategories();
      loadThreads();
    };
    categoryFilterList.appendChild(item);
  });

  const allItem = categoryFilterList.querySelector('[data-id=""]');
  if (allItem) {
    allItem.onclick = () => {
      activeCategoryId = null;
      loadCategories();
      loadThreads();
    };
  }
}

async function loadThreads(cursor = null) {
  if (!threadContainer) return;
  if (!cursor) {
    threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">[QUERYING_GATEWAY...]</div>`;
  }

  const res = await api.getThreads(20, cursor, activeCategoryId);
  if (!res.ok) {
    threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--destructive); font-family: var(--font-mono); font-size: 0.8rem;">[FEED_ERROR: ${res.error?.message || "Failed to retrieve threads"}]</div>`;
    return;
  }

  const threads = res.data.items || [];
  currentThreadListCursor = res.data.next_cursor || null;

  if (threads.length === 0) {
    threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">[EMPTY_SET: No threads published in this channel]</div>`;
    if (feedPagination) feedPagination.style.display = "none";
    return;
  }

  threadContainer.innerHTML = "";
  threads.forEach((t) => {
    const card = document.createElement("div");
    card.className = "thread-card";

    // Build Status Tokens
    let tokens = "";
    if (t.is_pinned) tokens += `<span class="token token-pinned">PINNED</span> `;
    if (t.is_locked) tokens += `<span class="token token-locked">LOCKED</span> `;
    if (t.best_answer_id) tokens += `<span class="token token-resolved">RESOLVED</span> `;

    card.innerHTML = `
      <div class="thread-vote-col">
        <button class="vote-btn vote-up-btn" data-id="${t.id}">▲</button>
        <span class="vote-counter" id="thread-vote-cnt-${t.id}">${t.score ?? t.upvotes ?? 0}</span>
        <button class="vote-btn vote-down-btn" data-id="${t.id}">▼</button>
      </div>
      <div class="thread-main-col">
        <div>
          ${tokens}
          <a class="thread-title-link" data-id="${t.id}">${escapeHtml(t.title)}</a>
        </div>
        <div class="thread-meta-row">
          <span>AUTHOR: @${escapeHtml(t.author_username || t.author_id?.slice(0, 8) || "unknown")}</span>
          <span>DATE: ${new Date(t.created_at).toISOString().split("T")[0]}</span>
          <span>VIEWS: ${t.view_count ?? 0}</span>
          <span>COMMENTS: ${t.comment_count ?? 0}</span>
        </div>
      </div>
    `;

    // Click handler for opening thread detail
    card.querySelector(".thread-title-link").onclick = () => {
      openThreadDetail(t.id);
    };

    // Upvote / Downvote actions
    card.querySelector(".vote-up-btn").onclick = async (e) => {
      e.stopPropagation();
      if (!auth.isAuthenticated()) {
        showAlert("error", "Sign in required to record vote.");
        return;
      }
      const vRes = await api.voteThread(t.id, 1);
      if (vRes.ok && vRes.data) {
        document.getElementById(`thread-vote-cnt-${t.id}`).textContent = vRes.data.score ?? 0;
      } else {
        showAlert("error", vRes.error?.message || "Vote failed.");
      }
    };

    card.querySelector(".vote-down-btn").onclick = async (e) => {
      e.stopPropagation();
      if (!auth.isAuthenticated()) {
        showAlert("error", "Sign in required to record vote.");
        return;
      }
      const vRes = await api.voteThread(t.id, -1);
      if (vRes.ok && vRes.data) {
        document.getElementById(`thread-vote-cnt-${t.id}`).textContent = vRes.data.score ?? 0;
      } else {
        showAlert("error", vRes.error?.message || "Vote failed.");
      }
    };

    threadContainer.appendChild(card);
  });

  // Pagination bar handling
  if (feedPagination) {
    if (currentThreadListCursor) {
      feedPagination.style.display = "flex";
      paginationCursorVal.textContent = currentThreadListCursor.slice(0, 16) + "...";
    } else {
      feedPagination.style.display = "none";
    }
  }
}

if (btnPaginateNext) {
  btnPaginateNext.onclick = () => {
    if (currentThreadListCursor) {
      loadThreads(currentThreadListCursor);
    }
  };
}

// Debounced Search Input (~300ms)
let searchDebounceTimeout = null;
if (feedSearchInput) {
  let searchSeq = 0;
  feedSearchInput.addEventListener("input", (e) => {
    if (searchDebounceTimeout) clearTimeout(searchDebounceTimeout);
    const q = e.target.value.trim();
    if (!q) {
      loadThreads();
      return;
    }
    const currentSeq = ++searchSeq;
    searchDebounceTimeout = setTimeout(async () => {
      threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">[SEARCHING_INDEX: "${q}"...]</div>`;
      const res = await api.searchThreads(q, activeCategoryId);
      if (currentSeq !== searchSeq) return; // Discard stale response
      if (res.ok && res.data) {
        renderSearchResults(res.data.items || res.data || []);
      } else {
        threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--destructive); font-family: var(--font-mono); font-size: 0.8rem;">[SEARCH_FAILURE: ${res.error?.message || "Search query failed"}]</div>`;
      }
    }, 300);
  });
}

function renderSearchResults(threads) {
  if (threads.length === 0) {
    threadContainer.innerHTML = `<div class="crosshair-panel" style="padding: 2rem; text-align: center; color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem;">[NO_MATCHES_FOUND]</div>`;
    return;
  }
  threadContainer.innerHTML = "";
  threads.forEach((t) => {
    const threadId = t.thread_id || t.id;
    const card = document.createElement("div");
    card.className = "thread-card";
    card.innerHTML = `
      <div class="thread-vote-col">
        <span class="vote-counter">#</span>
      </div>
      <div class="thread-main-col">
        <a class="thread-title-link" data-id="${threadId}">${escapeHtml(t.title)}</a>
        <div class="thread-meta-row">
          <span>THREAD_ID: ${threadId}</span>
          ${t.snippet ? `<span>SNIPPET: ${escapeHtml(t.snippet)}</span>` : ""}
        </div>
      </div>
    `;
    card.querySelector(".thread-title-link").onclick = () => openThreadDetail(threadId);
    threadContainer.appendChild(card);
  });
}

// -----------------------------------------------------------------------------
// SCREEN 04: Thread Detail, Comments & Voting
// -----------------------------------------------------------------------------
const threadDetailContainer = document.getElementById("thread-detail-container");
const commentsContainer = document.getElementById("comments-container");
const formCreateComment = document.getElementById("form-create-comment");
const commentBodyInput = document.getElementById("comment-body-input");
const btnBackToFeed = document.getElementById("btn-back-to-feed");

if (btnBackToFeed) {
  btnBackToFeed.onclick = () => navigateTo("view-home");
}

async function openThreadDetail(threadId) {
  navigateTo("view-thread-detail");
  threadDetailContainer.innerHTML = `<div style="padding: 2rem; text-align: center; font-family: var(--font-mono); color: var(--text-muted);">[FETCHING_THREAD_SPECIFICATION: ${threadId}]</div>`;
  commentsContainer.innerHTML = "";

  // 1. Record View Asynchronously (Fire & forget)
  api.recordThreadView(threadId).catch(() => {});

  // 2. Fetch Thread
  const res = await api.getThread(threadId);
  if (!res.ok) {
    threadDetailContainer.innerHTML = `<div style="padding: 2rem; text-align: center; font-family: var(--font-mono); color: var(--destructive);">[ERROR: ${res.error?.message || "Thread not found"}]</div>`;
    return;
  }

  currentActiveThread = res.data;
  renderThreadDetail(currentActiveThread);
  loadComments(threadId);
}

function renderThreadDetail(t) {
  let tokens = "";
  if (t.is_pinned) tokens += `<span class="token token-pinned">PINNED</span> `;
  if (t.is_locked) tokens += `<span class="token token-locked">LOCKED</span> `;
  if (t.best_answer_id) tokens += `<span class="token token-resolved">RESOLVED</span> `;

  const isAuthor = auth.currentUser && (auth.currentUser.id === t.author_id || auth.currentUser.username === t.author_username);
  const isMod = auth.isModerator();

  let actions = "";
  if (auth.isAuthenticated()) {
    actions += `<button class="btn btn-sm" id="btn-detail-bookmark">BOOKMARK</button> `;
    actions += `<button class="btn btn-sm" id="btn-detail-flag">FLAG</button> `;
  }
  if (isMod) {
    actions += `<button class="btn btn-sm" id="btn-detail-pin">${t.is_pinned ? "UNPIN" : "PIN"}</button> `;
    actions += `<button class="btn btn-sm" id="btn-detail-lock">${t.is_locked ? "UNLOCK" : "LOCK"}</button> `;
  }
  if (isAuthor || isMod) {
    actions += `<button class="btn btn-sm btn-destructive" id="btn-detail-delete">DELETE</button> `;
  }

  threadDetailContainer.innerHTML = `
    <div class="thread-detail-header">
      <div style="margin-bottom: 0.5rem;">${tokens}</div>
      <h1 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 0.5rem;">${escapeHtml(t.title)}</h1>
      <div class="thread-meta-row" style="margin-bottom: 1rem;">
        <span>THREAD_ID: ${t.id}</span>
        <span>AUTHOR: @${escapeHtml(t.author_username || t.author_id?.slice(0, 8) || "operator")}</span>
        <span>PUBLISHED: ${new Date(t.created_at).toISOString()}</span>
        <span>VIEWS: ${t.view_count ?? 0}</span>
      </div>
      <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
        ${actions}
      </div>
    </div>
    <div class="thread-detail-body markdown-content">
      ${safeMarkdownToHtml(t.body || t.content || "")}
    </div>
  `;

  // Attach button actions
  const btnBookmark = document.getElementById("btn-detail-bookmark");
  if (btnBookmark) {
    btnBookmark.onclick = async () => {
      const bRes = await api.bookmarkThread(t.id);
      if (bRes.ok) showAlert("success", "Thread saved to bookmarks dossier.");
      else showAlert("error", bRes.error?.message || "Bookmark action failed.");
    };
  }

  const btnFlag = document.getElementById("btn-detail-flag");
  if (btnFlag) {
    btnFlag.onclick = () => openReportDrawer("thread", t.id, t.title);
  }

  const btnPin = document.getElementById("btn-detail-pin");
  if (btnPin) {
    btnPin.onclick = async () => {
      const pRes = await api.pinThread(t.id, !t.is_pinned);
      if (pRes.ok) {
        showAlert("success", `Thread pin status modified.`);
        openThreadDetail(t.id);
      } else {
        showAlert("error", pRes.error?.message || "Pin mutation failed.");
      }
    };
  }

  const btnLock = document.getElementById("btn-detail-lock");
  if (btnLock) {
    btnLock.onclick = async () => {
      const lRes = await api.lockThread(t.id, !t.is_locked, "Operator action");
      if (lRes.ok) {
        showAlert("success", `Thread lock status modified.`);
        openThreadDetail(t.id);
      } else {
        showAlert("error", lRes.error?.message || "Lock mutation failed.");
      }
    };
  }

  const btnDelete = document.getElementById("btn-detail-delete");
  if (btnDelete) {
    btnDelete.onclick = async () => {
      if (!confirm("Confirm deletion of this thread specification?")) return;
      const dRes = await api.deleteThread(t.id);
      if (dRes.ok) {
        showAlert("success", "Thread resource purged.");
        navigateTo("view-home");
      } else if (dRes.status === 409) {
        // Trigger 409 Moderation Conflict Modal
        showConflictModal(dRes.error?.message || "Thread cannot be deleted because active comments exist.", t.id);
      } else {
        showAlert("error", dRes.error?.message || "Delete failed.");
      }
    };
  }
}

async function loadComments(threadId) {
  if (!commentsContainer) return;
  commentsContainer.innerHTML = `<div style="font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-muted);">[QUERYING_COMMENTS_STREAM...]</div>`;

  const res = await api.getComments(threadId);
  if (!res.ok) {
    commentsContainer.innerHTML = `<div style="font-family: var(--font-mono); font-size: 0.8rem; color: var(--destructive);">[COMMENTS_LOAD_FAILED]</div>`;
    return;
  }

  const comments = res.data.items || res.data || [];
  if (comments.length === 0) {
    commentsContainer.innerHTML = `<div style="font-family: var(--font-mono); font-size: 0.8rem; color: var(--text-muted); padding: 1rem 0;">[NO_RESPONSES_RECORDED_YET]</div>`;
    return;
  }

  commentsContainer.innerHTML = "";
  comments.forEach((c) => {
    const isBest = currentActiveThread?.best_answer_id === c.id;
    const isAuthor = currentActiveThread && (auth.currentUser?.id === currentActiveThread.author_id || auth.currentUser?.username === currentActiveThread.author_username);

    const cNode = document.createElement("div");
    cNode.className = `comment-node ${isBest ? "best-answer" : ""}`;
    cNode.innerHTML = `
      <div class="comment-meta">
        <span>@${escapeHtml(c.author_username || c.author_id?.slice(0, 8) || "user")}</span>
        <span>${new Date(c.created_at).toISOString().split("T")[0]}</span>
        ${isBest ? `<span class="token token-resolved">BEST_ANSWER</span>` : ""}
        <button class="btn btn-sm vote-c-btn" data-id="${c.id}" style="padding: 1px 5px;">▲ ${c.score ?? 0}</button>
        ${isAuthor && !isBest ? `<button class="btn btn-sm btn-mark-best" data-id="${c.id}" style="padding: 1px 5px;">MARK BEST</button>` : ""}
        <button class="btn btn-sm btn-flag-c" data-id="${c.id}" style="padding: 1px 5px;">FLAG</button>
      </div>
      <div class="markdown-content" style="font-size: 0.875rem;">
        ${safeMarkdownToHtml(c.body || c.content || "")}
      </div>
    `;

    cNode.querySelector(".vote-c-btn").onclick = async () => {
      if (!auth.isAuthenticated()) {
        showAlert("error", "Sign in required to vote.");
        return;
      }
      const vRes = await api.voteComment(c.id, 1);
      if (vRes.ok) loadComments(threadId);
      else showAlert("error", vRes.error?.message || "Vote failed.");
    };

    const markBestBtn = cNode.querySelector(".btn-mark-best");
    if (markBestBtn) {
      markBestBtn.onclick = async () => {
        const bRes = await api.setBestAnswer(threadId, c.id);
        if (bRes.ok) {
          showAlert("success", "Marked as verified resolution.");
          openThreadDetail(threadId);
        } else {
          showAlert("error", bRes.error?.message || "Failed to set best answer.");
        }
      };
    }

    cNode.querySelector(".btn-flag-c").onclick = () => {
      openReportDrawer("comment", c.id, `Comment by ${c.author_username || c.author_id}`);
    };

    commentsContainer.appendChild(cNode);
  });
}

if (formCreateComment) {
  formCreateComment.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!auth.isAuthenticated()) {
      showAlert("error", "Sign in required to submit comments.");
      return;
    }
    if (!currentActiveThread) return;

    const body = commentBodyInput.value.trim();
    if (!body) return;

    const res = await api.createComment(currentActiveThread.id, body);
    if (res.ok) {
      showAlert("success", "Comment appended to discussion stream.");
      commentBodyInput.value = "";
      loadComments(currentActiveThread.id);
    } else {
      showAlert("error", res.error?.message || "Failed to post comment.");
    }
  });
}

// -----------------------------------------------------------------------------
// SCREEN 05: Thread Editor (Creation)
// -----------------------------------------------------------------------------
const formCreateThread = document.getElementById("form-create-thread");
const threadTitleInput = document.getElementById("thread-title");
const threadCategorySelect = document.getElementById("thread-category");
const threadContentInput = document.getElementById("thread-content");
const titleCharCounter = document.getElementById("title-char-counter");
const bodyCharCounter = document.getElementById("body-char-counter");
const btnCancelCreateThread = document.getElementById("btn-cancel-create-thread");

if (btnCancelCreateThread) {
  btnCancelCreateThread.onclick = () => navigateTo("view-home");
}

if (threadTitleInput && titleCharCounter) {
  threadTitleInput.addEventListener("input", (e) => {
    titleCharCounter.textContent = `${e.target.value.length} / 200`;
  });
}

if (threadContentInput && bodyCharCounter) {
  threadContentInput.addEventListener("input", (e) => {
    bodyCharCounter.textContent = `${e.target.value.length} / 50000`;
  });
}

// Markdown formatting helpers
const mdBtnBold = document.getElementById("md-btn-bold");
const mdBtnCode = document.getElementById("md-btn-code");
const mdBtnQuote = document.getElementById("md-btn-quote");

function insertMarkdownWrap(wrapStart, wrapEnd) {
  if (!threadContentInput) return;
  const start = threadContentInput.selectionStart ?? 0;
  const end = threadContentInput.selectionEnd ?? 0;
  const text = threadContentInput.value;
  const selected = text.substring(start, end);
  threadContentInput.value = text.substring(0, start) + wrapStart + selected + wrapEnd + text.substring(end);
  const newCursorPos = start + wrapStart.length + selected.length;
  threadContentInput.setSelectionRange(newCursorPos, newCursorPos);
  threadContentInput.focus();
  if (bodyCharCounter) {
    bodyCharCounter.textContent = `${threadContentInput.value.length} / 50000`;
  }
}

if (mdBtnBold) mdBtnBold.onclick = () => insertMarkdownWrap("**", "**");
if (mdBtnCode) mdBtnCode.onclick = () => insertMarkdownWrap("`", "`");
if (mdBtnQuote) mdBtnQuote.onclick = () => insertMarkdownWrap("> ", "");

// Image Uploads for Thread Editor
const threadImageInput = document.getElementById("thread-image-input");
const btnUploadImage = document.getElementById("btn-upload-image");
const threadAttachmentsList = document.getElementById("thread-attachments-list");
const uploadStatus = document.getElementById("upload-status");

let pendingAttachmentIds = [];

function renderAttachmentChips() {
  if (!threadAttachmentsList) return;
  threadAttachmentsList.innerHTML = "";
  pendingAttachmentIds.forEach((att) => {
    const chip = document.createElement("div");
    chip.style = "display: inline-flex; align-items: center; gap: 0.5rem; background: var(--bg-surface-elevated, #1a1a24); border: 1px solid var(--border-color, #2d2d3d); padding: 0.25rem 0.5rem; font-family: var(--font-mono); font-size: 0.75rem;";
    chip.innerHTML = `
      <span>IMG: ${att.id.slice(0, 8)} (${(att.file_size / 1024).toFixed(1)} KB)</span>
      <button type="button" style="background: none; border: none; color: var(--color-danger, #ff5555); cursor: pointer; font-size: 0.9rem;" title="Remove">&times;</button>
    `;
    chip.querySelector("button").onclick = () => {
      pendingAttachmentIds = pendingAttachmentIds.filter((item) => item.id !== att.id);
      renderAttachmentChips();
    };
    threadAttachmentsList.appendChild(chip);
  });
}

if (btnUploadImage && threadImageInput) {
  btnUploadImage.onclick = async () => {
    const file = threadImageInput.files?.[0];
    if (!file) {
      showAlert("error", "Please select an image file to upload.");
      return;
    }

    if (file.size > 2 * 1024 * 1024) {
      showAlert("error", "Image size exceeds 2 MB limit.");
      return;
    }

    const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!allowedTypes.includes(file.type)) {
      showAlert("error", "Only JPG, PNG, and WebP images are supported.");
      return;
    }

    btnUploadImage.disabled = true;
    if (uploadStatus) uploadStatus.textContent = "Uploading...";

    try {
      const res = await api.uploadImage(file);
      if (res.ok && res.data?.id) {
        pendingAttachmentIds.push(res.data);
        renderAttachmentChips();
        threadImageInput.value = "";
        if (uploadStatus) uploadStatus.textContent = "Uploaded successfully.";
        // Automatically insert markdown image tag into body
        const imgMd = `\n![attachment](/api/v1/uploads/images/${res.data.id})\n`;
        if (threadContentInput) {
          threadContentInput.value += imgMd;
          if (bodyCharCounter) {
            bodyCharCounter.textContent = `${threadContentInput.value.length} / 50000`;
          }
        }
        showAlert("success", "Image uploaded and attached to markdown specification.");
      } else {
        const msg = res.error?.message || "Failed to upload image.";
        showAlert("error", msg);
        if (uploadStatus) uploadStatus.textContent = `Upload failed: ${msg}`;
      }
    } catch (err) {
      showAlert("error", `Upload failed: ${err.message}`);
      if (uploadStatus) uploadStatus.textContent = "Upload failed.";
    } finally {
      btnUploadImage.disabled = false;
    }
  };
}

async function loadCategoriesForThreadCreation() {
  if (!threadCategorySelect) return;
  const res = await api.getCategories();
  if (res.ok && res.data) {
    const categories = res.data.items || res.data || [];
    threadCategorySelect.innerHTML = categories.map((c) => `<option value="${c.id}">${c.name}</option>`).join("");
  }
}

if (formCreateThread) {
  formCreateThread.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = threadTitleInput.value.trim();
    const body = threadContentInput.value.trim();
    const categoryId = threadCategorySelect.value;

    if (!title || !body || !categoryId) {
      showAlert("error", "Validation error: Missing required fields.");
      return;
    }

    const imageIds = pendingAttachmentIds.map((a) => a.id);
    const res = await api.createThread(title, body, categoryId, imageIds);
    if (res.ok && res.data?.id) {
      showAlert("success", "Discussion thread published successfully.");
      formCreateThread.reset();
      pendingAttachmentIds = [];
      renderAttachmentChips();
      if (uploadStatus) uploadStatus.textContent = "";
      navigateTo("view-home", true);
    } else {
      showAlert("error", `Failed to publish thread: ${res.error?.message || "Internal error."}`);
    }
  });
}

// -----------------------------------------------------------------------------
// SCREEN 06: Moderation Operations & RBAC Management
// -----------------------------------------------------------------------------
const modReportsTbody = document.getElementById("moderation-reports-tbody");
const adminUserSection = document.getElementById("admin-user-section");
const adminUserTbody = document.getElementById("admin-user-tbody");
const modTabPending = document.getElementById("mod-tab-pending");
const modTabClaimed = document.getElementById("mod-tab-claimed");
const modTabResolved = document.getElementById("mod-tab-resolved");

let activeModStatus = "pending";

function updateModTabButtons() {
  if (modTabPending) {
    modTabPending.className = `btn btn-sm ${activeModStatus === "pending" ? "btn-primary" : ""}`;
  }
  if (modTabClaimed) {
    modTabClaimed.className = `btn btn-sm ${activeModStatus === "reviewing" ? "btn-primary" : ""}`;
  }
  if (modTabResolved) {
    modTabResolved.className = `btn btn-sm ${activeModStatus === "actioned" ? "btn-primary" : ""}`;
  }
}

if (modTabPending) {
  modTabPending.onclick = () => {
    activeModStatus = "pending";
    updateModTabButtons();
    loadModerationQueue();
  };
}
if (modTabClaimed) {
  modTabClaimed.onclick = () => {
    activeModStatus = "reviewing";
    updateModTabButtons();
    loadModerationQueue();
  };
}
if (modTabResolved) {
  modTabResolved.onclick = () => {
    activeModStatus = "actioned";
    updateModTabButtons();
    loadModerationQueue();
  };
}

async function loadAdminDashboard() {
  loadModerationQueue();
  if (auth.isAdmin()) {
    if (adminUserSection) adminUserSection.style.display = "block";
    loadAdminUsers();
  } else {
    if (adminUserSection) adminUserSection.style.display = "none";
  }
}

async function loadModerationQueue() {
  if (!modReportsTbody) return;
  modReportsTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">[QUERYING_AUDIT_LOGS: status=${activeModStatus}...]</td></tr>`;

  const res = await api.getModerationReports(activeModStatus);
  if (!res.ok) {
    modReportsTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--destructive);">[ERROR_LOADING_REPORTS: ${res.error?.message || "Forbidden"}]</td></tr>`;
    return;
  }

  const reports = res.data.items || [];
  if (reports.length === 0) {
    modReportsTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">[ZERO_REPORTS_PENDING_FOR_STATUS: ${activeModStatus}]</td></tr>`;
    return;
  }

  modReportsTbody.innerHTML = "";
  reports.forEach((rep) => {
    const tr = document.createElement("tr");

    let actionBtns = "";
    if (rep.status === "pending") {
      actionBtns = `<button class="btn btn-sm btn-claim-report" data-id="${rep.id}">CLAIM</button>`;
    } else if (rep.status === "reviewing") {
      actionBtns = `
        <button class="btn btn-sm btn-resolve-dismiss" data-id="${rep.id}">DISMISS</button>
        <button class="btn btn-sm btn-destructive btn-resolve-delete" data-id="${rep.id}">DELETE_CONTENT</button>
      `;
    } else {
      actionBtns = `<span style="color: var(--text-muted);">${rep.status.toUpperCase()}</span>`;
    }

    tr.innerHTML = `
      <td>${rep.id.slice(0, 8)}...</td>
      <td><span class="token">${rep.target_type}</span> ${rep.target_id.slice(0, 8)}...</td>
      <td>${escapeHtml(rep.reason)}</td>
      <td><span class="token token-${rep.status === "resolved" ? "resolved" : "pending"}">${rep.status}</span></td>
      <td>${new Date(rep.created_at).toISOString().split("T")[0]}</td>
      <td>${actionBtns}</td>
    `;

    const btnClaim = tr.querySelector(".btn-claim-report");
    if (btnClaim) {
      btnClaim.onclick = async () => {
        const cRes = await api.claimReport(rep.id);
        if (cRes.ok) {
          showAlert("success", `Report ${rep.id} claimed for review.`);
          loadModerationQueue();
        } else {
          showAlert("error", cRes.error?.message || "Failed to claim.");
        }
      };
    }

    const btnDismiss = tr.querySelector(".btn-resolve-dismiss");
    if (btnDismiss) {
      btnDismiss.onclick = async () => {
        const rRes = await api.resolveReport(rep.id, "dismiss_report", "Report reviewed: No policy violation detected");
        if (rRes.ok) {
          showAlert("success", `Report marked resolved (Dismissed).`);
          loadModerationQueue();
        } else {
          showAlert("error", rRes.error?.message || "Failed to resolve.");
        }
      };
    }

    const btnResolveDel = tr.querySelector(".btn-resolve-delete");
    if (btnResolveDel) {
      btnResolveDel.onclick = async () => {
        if (!confirm("Execute destructive purge of target content?")) return;
        const rRes = await api.resolveReport(rep.id, "soft_delete", "Violation confirmed by moderator: content purged");
        if (rRes.ok) {
          showAlert("success", `Content removed and report resolved.`);
          loadModerationQueue();
        } else {
          showAlert("error", rRes.error?.message || "Resolution failed.");
        }
      };
    }

    modReportsTbody.appendChild(tr);
  });
}

async function loadAdminUsers() {
  if (!adminUserTbody) return;
  const res = await api.getAdminUsers(30);
  if (!res.ok) {
    adminUserTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--destructive);">${res.error?.message || "Failed to load accounts"}</td></tr>`;
    return;
  }

  const users = res.data.items || [];
  adminUserTbody.innerHTML = "";

  users.forEach((u) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>@${escapeHtml(u.username)}</strong></td>
      <td>${escapeHtml(u.email || "-")}</td>
      <td><span class="token token-${u.role}">${u.role}</span></td>
      <td>${u.status || "active"}</td>
      <td>
        <select class="form-control select-role-change" data-id="${u.id}" style="width: 120px; padding: 2px 4px; font-size: 0.75rem;">
          <option value="member" ${u.role === "member" ? "selected" : ""}>MEMBER</option>
          <option value="moderator" ${u.role === "moderator" ? "selected" : ""}>MODERATOR</option>
          <option value="admin" ${u.role === "admin" ? "selected" : ""}>ADMIN</option>
        </select>
      </td>
    `;

    const select = tr.querySelector(".select-role-change");
    select.addEventListener("change", async (e) => {
      const newRole = e.target.value;
      if (!confirm(`Mutate role for @${u.username} to ${newRole.toUpperCase()}?`)) {
        select.value = u.role;
        return;
      }
      const mRes = await api.changeUserRole(u.id, newRole);
      if (mRes.ok) {
        showAlert("success", `Account @${u.username} granted ${newRole} privilege.`);
        loadAdminUsers();
      } else {
        showAlert("error", mRes.error?.message || "Failed to mutate role.");
        select.value = u.role;
      }
    });

    adminUserTbody.appendChild(tr);
  });
}

// -----------------------------------------------------------------------------
// SCREEN 07: User Profile, Reputation, Password Mutation & Bookmarks
// -----------------------------------------------------------------------------
const profileDetails = document.getElementById("profile-details");
const userBookmarksList = document.getElementById("user-bookmarks-list");
const btnProfileLogout = document.getElementById("btn-profile-logout");
const formChangePassword = document.getElementById("form-change-password");
const inputCurrentPassword = document.getElementById("input-current-password");
const inputNewPassword = document.getElementById("input-new-password");
const inputConfirmPassword = document.getElementById("input-confirm-password");
const changePasswordError = document.getElementById("change-password-error");
const btnSubmitChangePassword = document.getElementById("btn-submit-change-password");

if (formChangePassword) {
  formChangePassword.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (changePasswordError) {
      changePasswordError.style.display = "none";
      changePasswordError.textContent = "";
    }

    const currentPassword = inputCurrentPassword ? inputCurrentPassword.value : "";
    const newPassword = inputNewPassword ? inputNewPassword.value : "";
    const confirmPassword = inputConfirmPassword ? inputConfirmPassword.value : "";

    if (!currentPassword) {
      if (changePasswordError) {
        changePasswordError.textContent = "Current password is required.";
        changePasswordError.style.display = "block";
      }
      return;
    }

    if (newPassword.length < 8) {
      if (changePasswordError) {
        changePasswordError.textContent = "New password must be at least 8 characters.";
        changePasswordError.style.display = "block";
      }
      return;
    }

    if (newPassword !== confirmPassword) {
      if (changePasswordError) {
        changePasswordError.textContent = "New password and confirmation do not match.";
        changePasswordError.style.display = "block";
      }
      return;
    }

    if (currentPassword === newPassword) {
      if (changePasswordError) {
        changePasswordError.textContent = "New password cannot be identical to current password.";
        changePasswordError.style.display = "block";
      }
      return;
    }

    if (btnSubmitChangePassword) {
      btnSubmitChangePassword.disabled = true;
      btnSubmitChangePassword.textContent = "UPDATING...";
    }

    try {
      const res = await api.changePassword(currentPassword, newPassword);
      if (res.ok) {
        showAlert("success", "Password updated successfully. Other active sessions revoked.");
        formChangePassword.reset();
        if (changePasswordError) {
          changePasswordError.style.display = "none";
        }
      } else {
        const errorMsg = res.error?.message || "Failed to update password.";
        if (changePasswordError) {
          changePasswordError.textContent = errorMsg;
          changePasswordError.style.display = "block";
        }
        showAlert("error", errorMsg);
      }
    } catch (err) {
      const errorMsg = err.message || "An unexpected error occurred.";
      if (changePasswordError) {
        changePasswordError.textContent = errorMsg;
        changePasswordError.style.display = "block";
      }
      showAlert("error", errorMsg);
    } finally {
      if (btnSubmitChangePassword) {
        btnSubmitChangePassword.disabled = false;
        btnSubmitChangePassword.textContent = "UPDATE PASSWORD";
      }
    }
  });
}

if (btnProfileLogout) {
  btnProfileLogout.onclick = async () => {
    await auth.logout();
    showAlert("info", "Successfully logged out. Session terminated.");
    navigateTo("view-home", true);
  };
}

// -----------------------------------------------------------------------------
// Avatar Lifecycle Handlers (Task 030)
// -----------------------------------------------------------------------------
const btnUploadAvatar = document.getElementById("btn-upload-avatar");
const btnDeleteAvatar = document.getElementById("btn-delete-avatar");
const inputAvatarFile = document.getElementById("input-avatar-file");
const avatarStatusMsg = document.getElementById("avatar-status-msg");

if (btnUploadAvatar && inputAvatarFile) {
  btnUploadAvatar.onclick = () => {
    inputAvatarFile.click();
  };

  inputAvatarFile.onchange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (avatarStatusMsg) {
      avatarStatusMsg.textContent = "UPLOADING...";
      avatarStatusMsg.style.color = "var(--text-muted)";
    }

    try {
      const uploadRes = await api.uploadImage(file);
      if (!uploadRes.ok) {
        if (avatarStatusMsg) {
          avatarStatusMsg.textContent = uploadRes.error?.message || "Upload failed.";
          avatarStatusMsg.style.color = "var(--destructive)";
        }
        return;
      }

      const imageId = uploadRes.data.id;
      if (avatarStatusMsg) avatarStatusMsg.textContent = "ASSIGNING AVATAR...";

      const setRes = await api.setAvatar(imageId);
      if (!setRes.ok) {
        if (avatarStatusMsg) {
          avatarStatusMsg.textContent = setRes.error?.message || "Failed to set avatar.";
          avatarStatusMsg.style.color = "var(--destructive)";
        }
        return;
      }

      if (avatarStatusMsg) {
        avatarStatusMsg.textContent = "AVATAR UPDATED";
        avatarStatusMsg.style.color = "var(--success)";
      }
      showAlert("success", "Avatar updated successfully.");
      await loadProfileDetails();
    } catch (err) {
      if (avatarStatusMsg) {
        avatarStatusMsg.textContent = err.message || "Failed to upload avatar.";
        avatarStatusMsg.style.color = "var(--destructive)";
      }
    } finally {
      inputAvatarFile.value = "";
    }
  };
}

if (btnDeleteAvatar) {
  btnDeleteAvatar.onclick = async () => {
    if (avatarStatusMsg) {
      avatarStatusMsg.textContent = "REMOVING...";
      avatarStatusMsg.style.color = "var(--text-muted)";
    }
    try {
      const res = await api.deleteAvatar();
      if (res.ok) {
        if (avatarStatusMsg) {
          avatarStatusMsg.textContent = "AVATAR REMOVED";
          avatarStatusMsg.style.color = "var(--text-muted)";
        }
        showAlert("info", "Avatar removed.");
        await loadProfileDetails();
      } else {
        if (avatarStatusMsg) {
          avatarStatusMsg.textContent = res.error?.message || "Failed to remove avatar.";
          avatarStatusMsg.style.color = "var(--destructive)";
        }
      }
    } catch (err) {
      if (avatarStatusMsg) {
        avatarStatusMsg.textContent = err.message || "Failed to remove avatar.";
        avatarStatusMsg.style.color = "var(--destructive)";
      }
    }
  };
}

// -----------------------------------------------------------------------------
// Email Change Lifecycle Handlers (Task 030)
// -----------------------------------------------------------------------------
const formRequestEmailChange = document.getElementById("form-request-email-change");
const inputNewEmail = document.getElementById("input-new-email");
const inputEmailChangePassword = document.getElementById("input-email-change-password");
const emailChangeError = document.getElementById("email-change-error");
const btnSubmitEmailChange = document.getElementById("btn-submit-email-change");

const emailVerifyStep = document.getElementById("email-verify-step");
const targetEmailDisplay = document.getElementById("target-email-display");
const formVerifyEmailChange = document.getElementById("form-verify-email-change");
const inputEmailOtp = document.getElementById("input-email-otp");
const emailVerifyError = document.getElementById("email-verify-error");
const emailVerifySuccess = document.getElementById("email-verify-success");
const btnSubmitEmailVerify = document.getElementById("btn-submit-email-verify");
const btnResendEmailOtp = document.getElementById("btn-resend-email-otp");
const btnCancelEmailChange = document.getElementById("btn-cancel-email-change");

let pendingChangeEmail = "";

if (formRequestEmailChange) {
  formRequestEmailChange.onsubmit = async (e) => {
    e.preventDefault();
    if (emailChangeError) {
      emailChangeError.style.display = "none";
      emailChangeError.textContent = "";
    }

    const newEmail = inputNewEmail ? inputNewEmail.value.trim() : "";
    const password = inputEmailChangePassword ? inputEmailChangePassword.value : "";

    if (!newEmail || !password) return;

    if (btnSubmitEmailChange) {
      btnSubmitEmailChange.disabled = true;
      btnSubmitEmailChange.textContent = "SENDING...";
    }

    try {
      const res = await api.requestEmailChange(newEmail, password);
      if (res.ok) {
        pendingChangeEmail = newEmail;
        if (formRequestEmailChange) formRequestEmailChange.style.display = "none";
        if (emailVerifyStep) emailVerifyStep.style.display = "block";
        if (targetEmailDisplay) targetEmailDisplay.textContent = newEmail;
        if (inputEmailOtp) inputEmailOtp.value = "";
        if (emailVerifyError) emailVerifyError.style.display = "none";
        showAlert("info", "Verification code sent to your new email address.");
      } else {
        const errorMsg = res.error?.message || "Failed to initiate email change.";
        if (emailChangeError) {
          emailChangeError.textContent = errorMsg;
          emailChangeError.style.display = "block";
        }
      }
    } catch (err) {
      if (emailChangeError) {
        emailChangeError.textContent = err.message || "An unexpected error occurred.";
        emailChangeError.style.display = "block";
      }
    } finally {
      if (btnSubmitEmailChange) {
        btnSubmitEmailChange.disabled = false;
        btnSubmitEmailChange.textContent = "SEND VERIFICATION CODE";
      }
    }
  };
}

if (formVerifyEmailChange) {
  formVerifyEmailChange.onsubmit = async (e) => {
    e.preventDefault();
    if (emailVerifyError) {
      emailVerifyError.style.display = "none";
      emailVerifyError.textContent = "";
    }

    const otp = inputEmailOtp ? inputEmailOtp.value.trim() : "";
    if (!otp) return;

    if (btnSubmitEmailVerify) {
      btnSubmitEmailVerify.disabled = true;
      btnSubmitEmailVerify.textContent = "VERIFYING...";
    }

    try {
      const res = await api.verifyEmailChange(otp);
      if (res.ok) {
        showAlert("success", "Email address updated successfully.");
        if (emailVerifySuccess) {
          emailVerifySuccess.textContent = "Email successfully updated!";
          emailVerifySuccess.style.display = "block";
        }
        setTimeout(() => {
          if (emailVerifyStep) emailVerifyStep.style.display = "none";
          if (formRequestEmailChange) {
            formRequestEmailChange.reset();
            formRequestEmailChange.style.display = "block";
          }
          if (emailVerifySuccess) emailVerifySuccess.style.display = "none";
          loadProfileDetails();
        }, 1500);
      } else {
        const errorMsg = res.error?.message || "Verification failed.";
        if (emailVerifyError) {
          emailVerifyError.textContent = errorMsg;
          emailVerifyError.style.display = "block";
        }
      }
    } catch (err) {
      if (emailVerifyError) {
        emailVerifyError.textContent = err.message || "An unexpected error occurred.";
        emailVerifyError.style.display = "block";
      }
    } finally {
      if (btnSubmitEmailVerify) {
        btnSubmitEmailVerify.disabled = false;
        btnSubmitEmailVerify.textContent = "VERIFY & UPDATE EMAIL";
      }
    }
  };
}

if (btnResendEmailOtp) {
  btnResendEmailOtp.onclick = async () => {
    btnResendEmailOtp.disabled = true;
    btnResendEmailOtp.textContent = "RESENDING...";
    try {
      const res = await api.resendEmailChangeOtp();
      if (res.ok) {
        showAlert("info", "Verification code resent.");
      } else {
        const errorMsg = res.error?.message || "Failed to resend code.";
        if (emailVerifyError) {
          emailVerifyError.textContent = errorMsg;
          emailVerifyError.style.display = "block";
        }
      }
    } catch (err) {
      if (emailVerifyError) {
        emailVerifyError.textContent = err.message || "Failed to resend code.";
        emailVerifyError.style.display = "block";
      }
    } finally {
      btnResendEmailOtp.disabled = false;
      btnResendEmailOtp.textContent = "RESEND CODE";
    }
  };
}

if (btnCancelEmailChange) {
  btnCancelEmailChange.onclick = () => {
    if (emailVerifyStep) emailVerifyStep.style.display = "none";
    if (formRequestEmailChange) {
      formRequestEmailChange.reset();
      formRequestEmailChange.style.display = "block";
    }
    if (emailVerifyError) emailVerifyError.style.display = "none";
    if (emailChangeError) emailChangeError.style.display = "none";
  };
}

async function loadProfileDetails() {
  if (!profileDetails) return;
  profileDetails.innerHTML = `<p style="color: var(--text-muted);">[QUERYING_PROFILE_GATEWAY...]</p>`;

  const res = await api.getProfile();
  if (!res.ok) {
    profileDetails.innerHTML = `<p style="color: var(--destructive);">${res.error?.message || "Failed to load profile."}</p>`;
    return;
  }

  const u = res.data;
  let repScore = 0;
  try {
    const repRes = await api.getUserReputation(u.id);
    if (repRes.ok && repRes.data) {
      repScore = repRes.data.reputation_score ?? 0;
    }
  } catch (e) {
    console.warn("Could not query user reputation aggregate", e);
  }

  profileDetails.innerHTML = `
    <div><strong>ACCOUNT_ID:</strong> <span style="color: var(--text-muted);">${u.id}</span></div>
    <div><strong>USERNAME:</strong> @${escapeHtml(u.username)}</div>
    <div><strong>EMAIL:</strong> ${escapeHtml(u.email || "-")}</div>
    <div><strong>ROLE:</strong> <span class="token token-${u.role}">${u.role}</span></div>
    <div><strong>STATUS:</strong> ${u.status || "ACTIVE"}</div>
    <div><strong>AGGREGATE_REPUTATION:</strong> <span style="color: var(--success); font-weight: 700;">${repScore} PTS</span></div>
    <div style="font-size: 0.75rem; color: var(--text-muted); border-top: 1px solid var(--border); padding-top: 0.5rem; margin-top: 0.5rem;">
      NOTE: Itemized reputation transaction ledger is maintained internally by InteractionService. Aggregate score authoritative.
    </div>
  `;

  // Update Avatar display
  const avatarPreviewBox = document.getElementById("avatar-preview-box");
  const btnDeleteAvatar = document.getElementById("btn-delete-avatar");
  if (avatarPreviewBox) {
    if (u.avatar_url) {
      avatarPreviewBox.innerHTML = `<img src="${u.avatar_url}" alt="Avatar" style="width: 100%; height: 100%; object-fit: cover;" />`;
      if (btnDeleteAvatar) btnDeleteAvatar.style.display = "inline-flex";
    } else {
      avatarPreviewBox.innerHTML = `NO_AVATAR`;
      if (btnDeleteAvatar) btnDeleteAvatar.style.display = "none";
    }
  }

  // Load User Bookmarks
  loadUserBookmarks();
}

async function loadUserBookmarks() {
  if (!userBookmarksList) return;
  const res = await api.getBookmarks();
  if (!res.ok || !res.data) {
    userBookmarksList.innerHTML = `<p style="color: var(--text-muted); font-family: var(--font-mono);">[BOOKMARKS_NOT_AVAILABLE]</p>`;
    return;
  }

  const bookmarks = res.data.items || res.data || [];
  if (bookmarks.length === 0) {
    userBookmarksList.innerHTML = `<p style="color: var(--text-muted); font-family: var(--font-mono);">[NO_BOOKMARKS_RECORDED]</p>`;
    return;
  }

  userBookmarksList.innerHTML = "";
  bookmarks.forEach((b) => {
    const row = document.createElement("div");
    row.style.padding = "0.5rem 0";
    row.style.borderBottom = "1px solid var(--border)";
    row.style.display = "flex";
    row.style.justifyContent = "space-between";
    row.style.alignItems = "center";

    row.innerHTML = `
      <div>
        <a style="cursor: pointer; font-weight: 600;" class="bm-link">${escapeHtml(b.title || b.thread_id)}</a>
      </div>
      <div>
        <button class="btn btn-sm btn-destructive btn-rm-bm">REMOVE</button>
      </div>
    `;

    row.querySelector(".bm-link").onclick = () => openThreadDetail(b.thread_id || b.id);
    row.querySelector(".btn-rm-bm").onclick = async () => {
      const uRes = await api.unbookmarkThread(b.thread_id || b.id);
      if (uRes.ok) {
        showAlert("success", "Bookmark removed.");
        loadUserBookmarks();
      } else {
        showAlert("error", uRes.error?.message || "Failed to unbookmark.");
      }
    };

    userBookmarksList.appendChild(row);
  });
}

// -----------------------------------------------------------------------------
// Global Moderation Conflict Modal (409 Conflict)
// -----------------------------------------------------------------------------
const conflictModal = document.getElementById("conflict-modal");
const conflictMessage = document.getElementById("conflict-modal-message");
const btnConflictDismiss = document.getElementById("btn-conflict-dismiss");
const btnConflictOpenFlag = document.getElementById("btn-conflict-open-flag");
let conflictTargetId = null;

export function showConflictModal(msg, targetId) {
  conflictTargetId = targetId;
  if (conflictMessage) conflictMessage.textContent = msg;
  if (conflictModal) conflictModal.style.display = "flex";
}

export function hideConflictModal() {
  if (conflictModal) conflictModal.style.display = "none";
}

if (btnConflictDismiss) btnConflictDismiss.onclick = hideConflictModal;
if (btnConflictOpenFlag) {
  btnConflictOpenFlag.onclick = () => {
    hideConflictModal();
    if (conflictTargetId) {
      openReportDrawer("thread", conflictTargetId, `Deletion Conflict for ${conflictTargetId}`);
    }
  };
}

// -----------------------------------------------------------------------------
// Global Report Drawer
// -----------------------------------------------------------------------------
const reportDrawer = document.getElementById("report-drawer");
const btnCloseReportDrawer = document.getElementById("btn-close-report-drawer");
const formSubmitReport = document.getElementById("form-submit-report");
const reportTargetType = document.getElementById("report-target-type");
const reportTargetId = document.getElementById("report-target-id");
const reportTargetRef = document.getElementById("report-target-ref");
const reportReasonSelect = document.getElementById("report-reason-select");
const reportDetailsInput = document.getElementById("report-details-input");

export function openReportDrawer(type, id, label) {
  if (!auth.isAuthenticated()) {
    showAlert("error", "Sign in required to submit reports.");
    return;
  }
  if (reportTargetType) reportTargetType.value = type;
  if (reportTargetId) reportTargetId.value = id;
  if (reportTargetRef) reportTargetRef.textContent = `[${type.toUpperCase()}] ${label} (ID: ${id})`;
  if (reportDrawer) reportDrawer.classList.add("open");
}

export function closeReportDrawer() {
  if (reportDrawer) reportDrawer.classList.remove("open");
}

if (btnCloseReportDrawer) btnCloseReportDrawer.onclick = closeReportDrawer;

if (formSubmitReport) {
  formSubmitReport.addEventListener("submit", async (e) => {
    e.preventDefault();
    const type = reportTargetType.value;
    const id = reportTargetId.value;
    const reason = reportReasonSelect.value;
    const details = reportDetailsInput.value.trim();

    const res = await api.createReport(type, id, reason, details);
    if (res.ok) {
      showAlert("success", "Moderation report transmitted to operator queue.");
      closeReportDrawer();
      formSubmitReport.reset();
    } else {
      showAlert("error", res.error?.message || "Failed to submit report.");
    }
  });
}

// Helper: Escape HTML
export function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Expose globals for backward compatibility with automated test harness
window.escapeHtml = escapeHtml;
window.navigateTo = navigateTo;
window.safeMarkdownToHtml = safeMarkdownToHtml;

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------
async function initApp() {
  // Brand logo home link
  const brand = document.getElementById("brand-logo");
  if (brand) brand.onclick = () => navigateTo("view-home");

  // Verify existing session cookie via Gateway
  try {
    await auth.checkSession();
  } catch (e) {
    console.warn("Session check bypass:", e);
  }

  // Load initial view
  navigateTo("view-home");
}

window.addEventListener("DOMContentLoaded", initApp);
