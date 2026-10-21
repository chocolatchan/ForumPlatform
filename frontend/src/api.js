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

  // --- Content & Forum Endpoints ---

  async getCategories() {
    return this.request("/categories", {
      method: "GET",
    });
  }

  async getThreads(limit = 20, cursor = null) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (cursor) q.set("cursor", cursor);
    return this.request(`/threads?${q.toString()}`, {
      method: "GET",
    });
  }

  async createThread(title, body, categoryId) {
    return this.request("/threads", {
      method: "POST",
      body: { title, body, category_id: categoryId },
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
}
