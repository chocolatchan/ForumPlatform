/**
 * Centralized API Client for ForumPlatform Gateway
 * Targets /api/v1 through API Gateway (port 8000).
 * Handles cookie session authentication, CSRF synchronizer token management,
 * and canonical error parsing.
 */

export class ApiClient {
  constructor(baseUrl = "/api/v1") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.csrfToken = null;
  }

  /**
   * Fetch a fresh CSRF token from the API Gateway.
   */
  async fetchCsrfToken() {
    try {
      const resp = await fetch(`${this.baseUrl}/auth/csrf`, {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "include",
      });
      if (resp.ok) {
        const data = await resp.json();
        this.csrfToken = data.csrf_token;
        return this.csrfToken;
      }
    } catch (e) {
      console.warn("Failed to fetch CSRF token:", e);
    }
    return null;
  }

  /**
   * Generic API request wrapper with standard credentials and error handling.
   */
  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
    const headers = {
      "Accept": "application/json",
      ...(options.headers || {}),
    };

    // State-modifying methods require CSRF token
    const method = (options.method || "GET").toUpperCase();
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      if (!this.csrfToken) {
        await this.fetchCsrfToken();
      }
      if (this.csrfToken) {
        headers["X-CSRF-Token"] = this.csrfToken;
      }
      if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(options.body);
      }
    }

    try {
      const resp = await fetch(url, {
        ...options,
        headers,
        credentials: "include", // Preserves forum_session cookie
      });

      // Handle 204 No Content
      if (resp.status === 204) {
        return { ok: true, status: 204, data: null };
      }

      const contentType = resp.headers.get("content-type") || "";
      let data = null;
      if (contentType.includes("application/json")) {
        data = await resp.json();
      } else {
        const text = await resp.text();
        data = { message: text };
      }

      if (!resp.ok) {
        // Canonical error payload structure: { error: { code, message, details, request_id } }
        const errorCode = data?.error?.code || `HTTP_${resp.status}`;
        const errorMessage = data?.error?.message || data?.message || "An unexpected error occurred.";
        const errorDetails = data?.error?.details || [];

        return {
          ok: false,
          status: resp.status,
          error: {
            code: errorCode,
            message: errorMessage,
            details: errorDetails,
            raw: data,
          },
        };
      }

      return {
        ok: true,
        status: resp.status,
        data,
      };
    } catch (networkError) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "NETWORK_ERROR",
          message: networkError.message || "Network request failed or gateway unreachable.",
          details: [],
        },
      };
    }
  }

  // --- Auth Endpoints ---

  async register(username, email, password) {
    return this.request("/auth/register", {
      method: "POST",
      body: { username, email, password },
    });
  }

  async verifyOtp(email, otp) {
    return this.request("/auth/register/verify", {
      method: "POST",
      body: { email, otp },
    });
  }

  async resendOtp(email) {
    return this.request("/auth/register/resend-otp", {
      method: "POST",
      body: { email },
    });
  }

  async login(loginIdentifier, password) {
    return this.request("/auth/login", {
      method: "POST",
      body: { login: loginIdentifier, password },
    });
  }

  async logout() {
    return this.request("/auth/logout", {
      method: "POST",
    });
  }

  async getSession() {
    return this.request("/auth/session", {
      method: "GET",
    });
  }

  async getProfile() {
    return this.request("/profile", {
      method: "GET",
    });
  }

  async changePassword(currentPassword, newPassword) {
    return this.request("/profile/change-password", {
      method: "POST",
      body: {
        current_password: currentPassword,
        new_password: newPassword,
      },
    });
  }

  // --- Content & Forum Endpoints ---

  async getCategories() {
    return this.request("/categories", {
      method: "GET",
    });
  }

  async getThreads(limit = 20, cursor = null, categoryId = null) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (cursor) q.set("cursor", cursor);
    if (categoryId) q.set("category_id", categoryId);
    return this.request(`/threads?${q.toString()}`, {
      method: "GET",
    });
  }

  async getThread(threadId) {
    return this.request(`/threads/${threadId}`, {
      method: "GET",
    });
  }

  async recordThreadView(threadId) {
    return this.request(`/threads/${threadId}/view`, {
      method: "POST",
    });
  }

  async createThread(title, body, categoryId) {
    return this.request("/threads", {
      method: "POST",
      body: { title, body, category_id: categoryId },
    });
  }

  async lockThread(threadId, locked = true, reason = "") {
    return this.request(`/threads/${threadId}/lock`, {
      method: "PUT",
      body: { is_locked: locked },
    });
  }

  async pinThread(threadId, pinned = true) {
    return this.request(`/threads/${threadId}/pin`, {
      method: "PUT",
      body: { is_pinned: pinned },
    });
  }

  async deleteThread(threadId) {
    return this.request(`/threads/${threadId}`, {
      method: "DELETE",
    });
  }

  async getComments(threadId, limit = 50, cursor = null) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (cursor) q.set("cursor", cursor);
    return this.request(`/threads/${threadId}/comments?${q.toString()}`, {
      method: "GET",
    });
  }

  async createComment(threadId, body, parentId = null) {
    const payload = { body };
    if (parentId) payload.parent_id = parentId;
    return this.request(`/threads/${threadId}/comments`, {
      method: "POST",
      body: payload,
    });
  }

  async deleteComment(commentId) {
    return this.request(`/comments/${commentId}`, {
      method: "DELETE",
    });
  }

  // --- Interaction Endpoints ---

  async voteThread(threadId, value) {
    // value: 1 for upvote, -1 for downvote (toggling is handled by repeating same value)
    return this.request(`/threads/${threadId}/vote`, {
      method: "PUT",
      body: { value },
    });
  }

  async voteComment(commentId, value) {
    return this.request(`/comments/${commentId}/vote`, {
      method: "PUT",
      body: { value },
    });
  }

  async bookmarkThread(threadId) {
    return this.request(`/threads/${threadId}/bookmark`, {
      method: "PUT",
    });
  }

  async unbookmarkThread(threadId) {
    return this.request(`/threads/${threadId}/bookmark`, {
      method: "DELETE",
    });
  }

  async getBookmarks(limit = 20, cursor = null) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (cursor) q.set("cursor", cursor);
    return this.request(`/profile/bookmarks?${q.toString()}`, {
      method: "GET",
    });
  }

  async setBestAnswer(threadId, commentId) {
    return this.request(`/threads/${threadId}/best-answer`, {
      method: "PUT",
      body: { comment_id: commentId },
    });
  }

  // --- Search Endpoints ---

  async searchThreads(query, categoryId = null, tag = null, limit = 20) {
    const q = new URLSearchParams({ q: query, limit: String(limit) });
    if (categoryId) q.set("category_id", categoryId);
    if (tag) q.set("tag", tag);
    return this.request(`/search?${q.toString()}`, {
      method: "GET",
    });
  }

  async getSearchSuggestions(prefix) {
    const q = new URLSearchParams({ prefix });
    return this.request(`/search/suggestions?${q.toString()}`, {
      method: "GET",
    });
  }

  // --- User & Reputation Endpoints ---

  async getUserReputation(userId) {
    return this.request(`/users/${userId}/reputation`, {
      method: "GET",
    });
  }

  // --- Admin & Moderation Endpoints ---

  async getAdminUsers(limit = 20) {
    return this.request(`/admin/users?limit=${limit}`, {
      method: "GET",
    });
  }

  async changeUserRole(userId, newRole) {
    return this.request(`/admin/users/${userId}/role`, {
      method: "PATCH",
      body: { role: newRole },
    });
  }

  async getModerationReports(status = "pending", limit = 20) {
    return this.request(`/moderation/reports?status=${status}&limit=${limit}`, {
      method: "GET",
    });
  }

  async getReportDossier(reportId) {
    return this.request(`/moderation/reports/${reportId}`, {
      method: "GET",
    });
  }

  async claimReport(reportId) {
    return this.request(`/moderation/reports/${reportId}/review`, {
      method: "PATCH",
    });
  }

  async resolveReport(reportId, action, reasonNotes = "") {
    return this.request(`/moderation/reports/${reportId}/actions`, {
      method: "POST",
      body: { action, reason: reasonNotes },
    });
  }

  async createReport(targetType, targetId, reason, details = "") {
    return this.request("/reports", {
      method: "POST",
      body: {
        target_type: targetType,
        target_id: targetId,
        reason,
        details,
      },
    });
  }
}
