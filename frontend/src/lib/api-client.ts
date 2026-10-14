import { z } from "zod";
import { authSchema, type AuthSession } from "./contracts";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const errorSchema = z.object({
  detail: z.union([z.string(), z.array(z.object({ msg: z.string() }))]).optional()
});

export class ApiClient {
  private accessToken: string | null = null;
  private refreshFlight: Promise<AuthSession> | null = null;
  private generation = 0;
  private readonly sessionListeners = new Set<() => void>();

  onSessionExpired(listener: () => void): () => void {
    this.sessionListeners.add(listener);
    return () => { this.sessionListeners.delete(listener); };
  }

  setSession(session: AuthSession): void {
    this.generation += 1;
    this.accessToken = session.access_token;
  }

  clearSession(): void {
    this.generation += 1;
    this.accessToken = null;
    for (const listener of this.sessionListeners) listener();
  }

  private async send(path: string, options: RequestInit, authenticated: boolean): Promise<Response> {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (authenticated && this.accessToken) headers.set("Authorization", `Bearer ${this.accessToken}`);
    const method = (options.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && typeof document !== "undefined") {
      const cookie = document.cookie.split(";").map((value) => value.trim())
        .find((value) => value.startsWith("csrf_token="));
      if (cookie) {
        try {
          headers.set("X-CSRF-Token", decodeURIComponent(cookie.slice("csrf_token=".length)));
        } catch {
          throw new ApiError(401, "The session cookie is invalid. Clear this site's cookies and sign in again.");
        }
      }
    }
    try {
      return await fetch(`/api/v1${path}`, {
        ...options,
        headers,
        credentials: "include",
        cache: "no-store",
        signal: options.signal ?? AbortSignal.timeout(120_000)
      });
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError(0, "The server could not be reached or the request timed out.");
    }
  }

  private async decode<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
    const text = await response.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      throw new ApiError(response.status, "The server returned an invalid JSON response.");
    }
    if (!response.ok) {
      const parsed = errorSchema.safeParse(payload);
      const detail = parsed.success ? parsed.data.detail : undefined;
      const message = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join("; ")
          : `Request failed (${response.status}).`;
      throw new ApiError(response.status, message);
    }
    const result = schema.safeParse(payload);
    if (!result.success) throw new ApiError(502, "The server response did not match the API contract.");
    return result.data;
  }

  async refresh(): Promise<AuthSession> {
    if (this.refreshFlight) return this.refreshFlight;
    const generation = this.generation;
    this.refreshFlight = (async () => {
      try {
        const session = await this.decode(
          await this.send("/auth/refresh", { method: "POST" }, false),
          authSchema
        );
        if (generation !== this.generation) throw new ApiError(401, "The session changed. Sign in again.");
        this.accessToken = session.access_token;
        return session;
      } catch (error: unknown) {
        if (generation === this.generation) {
          this.accessToken = null;
          if (error instanceof ApiError && error.status === 401) this.clearSession();
        }
        throw error;
      } finally {
        this.refreshFlight = null;
      }
    })();
    return this.refreshFlight;
  }

  async request<T>(
    path: string,
    schema: z.ZodType<T>,
    options: RequestInit = {},
    authenticated = true
  ): Promise<T> {
    const tokenUsed = this.accessToken;
    const response = await this.send(path, options, authenticated);
    if (response.status !== 401 || !authenticated) return this.decode(response, schema);
    // Concurrent expired-token responses share one rotation, including late responses.
    if (!this.accessToken || tokenUsed === this.accessToken) await this.refresh();
    const retried = await this.send(path, options, authenticated);
    if (retried.status === 401) this.clearSession();
    return this.decode(retried, schema);
  }

  async logout(): Promise<void> {
    if (this.refreshFlight) {
      try {
        await this.refreshFlight;
      } catch (error: unknown) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error;
      }
    }
    const response = await this.send("/auth/logout", { method: "POST" }, false);
    if (!response.ok) await this.decode(response, z.unknown());
    this.clearSession();
  }
}

export const api = new ApiClient();
