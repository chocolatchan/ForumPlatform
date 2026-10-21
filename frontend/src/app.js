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

    const res = await api.createThread(title, body, categoryId);
    if (res.ok && res.data?.id) {
      showAlert("success", "Discussion thread published successfully.");
      formCreateThread.reset();
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
// SCREEN 07: User Profile, Reputation & Bookmarks
// -----------------------------------------------------------------------------
const profileDetails = document.getElementById("profile-details");
const userBookmarksList = document.getElementById("user-bookmarks-list");
const btnProfileLogout = document.getElementById("btn-profile-logout");

if (btnProfileLogout) {
  btnProfileLogout.onclick = async () => {
    await auth.logout();
    showAlert("info", "Successfully logged out. Session terminated.");
    navigateTo("view-home", true);
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
