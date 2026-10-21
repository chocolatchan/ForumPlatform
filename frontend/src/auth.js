/**
 * In-Memory Authentication & Session State Manager
 * Maintains user info, active role, and session expiration timestamps.
 * Does NOT persist credentials, plain passwords, or tokens in localStorage/sessionStorage.
 */

export class AuthManager {
  constructor(apiClient) {
    this.api = apiClient;
    this.currentUser = null;
    this.sessionInfo = null;
    this.listeners = [];
  }

  subscribe(listener) {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  notify() {
    for (const listener of this.listeners) {
      try {
        listener(this.currentUser, this.sessionInfo);
      } catch (e) {
        console.error("Auth listener error:", e);
      }
    }
  }

  isAuthenticated() {
    return this.currentUser !== null;
  }

  getUserRole() {
    return this.currentUser?.role || "guest";
  }

  isAdmin() {
    return this.getUserRole() === "admin";
  }

  isModerator() {
    const role = this.getUserRole();
    return role === "moderator" || role === "admin";
  }

  /**
   * Check active session against Gateway /auth/session or /profile.
   */
  async checkSession() {
    const res = await this.api.getSession();
    if (res.ok && res.data?.user) {
      this.currentUser = res.data.user;
      this.sessionInfo = res.data.session || null;
    } else {
      this.currentUser = null;
      this.sessionInfo = null;
    }
    this.notify();
    return this.currentUser;
  }

  /**
   * Set user upon successful login.
   */
  setAuthenticated(user, session) {
    this.currentUser = user;
    this.sessionInfo = session;
    this.notify();
  }

  /**
   * Clear state upon logout.
   */
  clear() {
    this.currentUser = null;
    this.sessionInfo = null;
    this.notify();
  }

  /**
   * Execute logout via API Gateway and clear in-memory state.
   */
  async logout() {
    const res = await this.api.logout();
    this.clear();
    return res;
  }
}
