import type { AuthTokens } from "./types";

const ACCESS_TOKEN_KEY = "delibrag.access_token";
const REFRESH_TOKEN_KEY = "delibrag.refresh_token";

function isBrowser() {
  return typeof window !== "undefined";
}

export function getAccessToken(): string | null {
  if (!isBrowser()) {
    return null;
  }
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (!isBrowser()) {
    return null;
  }
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(tokens: AuthTokens) {
  if (!isBrowser()) {
    return;
  }
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function clearTokens() {
  if (!isBrowser()) {
    return;
  }
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function authHeader() {
  const access = getAccessToken();
  if (!access) {
    return {} as Record<string, string>;
  }
  return { Authorization: `Bearer ${access}` };
}

export function refreshAuthHeader() {
  const refresh = getRefreshToken();
  if (!refresh) {
    return {} as Record<string, string>;
  }
  return { Authorization: `Bearer ${refresh}` };
}
