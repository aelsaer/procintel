import type { AccessControlProvider, AuthProvider } from "@refinedev/core";
import { ApiError, api } from "@/lib/api";
import { clearOidcSession, startLogoutRedirect } from "@/lib/oidc";

export const ACCESS_TOKEN_KEY = "procintel_access_token";
export const LOCAL_SESSION_KEY = "procintel_local_session";

function clearClientSession() {
  if (typeof window === "undefined") return;
  clearOidcSession();
  window.localStorage.removeItem(LOCAL_SESSION_KEY);
}

export const procurementAuthProvider: AuthProvider = {
  login: async ({ token, mode, redirectTo }: { token?: string; mode?: "local"; redirectTo?: string }) => {
    if (token && typeof window !== "undefined") window.localStorage.setItem(ACCESS_TOKEN_KEY, token);
    try {
      await api.getMe();
      if (mode === "local" && typeof window !== "undefined") {
        window.localStorage.setItem(LOCAL_SESSION_KEY, "true");
      }
      try {
        // §40.3 names "login" as an audited action; a failure to record it
        // must not fail a login that otherwise succeeded.
        await api.acknowledgeLogin();
      } catch {
        // best-effort
      }
      return { success: true, redirectTo: redirectTo ?? "/" };
    } catch (error) {
      if (token) clearClientSession();
      return { success: false, error: error as Error };
    }
  },
  logout: async () => {
    const oidcRedirectStarted = startLogoutRedirect();
    if (!oidcRedirectStarted) clearClientSession();
    return { success: true, redirectTo: oidcRedirectStarted ? undefined : "/login" };
  },
  check: async () => {
    if (typeof window !== "undefined") {
      const hasToken = Boolean(window.localStorage.getItem(ACCESS_TOKEN_KEY));
      const hasLocalSession = window.localStorage.getItem(LOCAL_SESSION_KEY) === "true";
      if (!hasToken && !hasLocalSession) return { authenticated: false };
    }
    try {
      await api.getMe();
      return { authenticated: true };
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) clearClientSession();
      return { authenticated: false, error: error as Error };
    }
  },
  getPermissions: async () => (await api.getMe()).role,
  getIdentity: async () => {
    const me = await api.getMe();
    return { id: me.subject, name: me.email ?? me.subject, role: me.role, tenant: me.tenant_name };
  },
  onError: async (error) => ({ error }),
};

const writeActions = new Set(["create", "edit", "delete"]);

export const procurementAccessControlProvider: AccessControlProvider = {
  can: async ({ action }) => {
    const me = await api.getMe();
    const forbidden = me.role === "VIEWER" && writeActions.has(action);
    return { can: !forbidden, reason: forbidden ? "Ο ρόλος VIEWER έχει μόνο δικαίωμα ανάγνωσης." : undefined };
  },
};
