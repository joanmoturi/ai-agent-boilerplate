import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { ApiClient, ApiError } from "./api-client";

const session = {
  access_token: "rotated-access-token",
  token_type: "bearer",
  user: { id: "11111111-1111-4111-8111-111111111111", email: "test@example.com" }
};
const valueSchema = z.object({ value: z.number() });
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" }
  });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("API client", () => {
  it("coalesces refresh when concurrent calls receive 401", async () => {
    let refreshCount = 0;
    const fetchMock = vi.fn(async (path: string, init: RequestInit) => {
      if (path.endsWith("/auth/refresh")) {
        refreshCount += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return json(session);
      }
      if (new Headers(init.headers).has("Authorization")) return json({ value: 42 });
      return json({ detail: "Expired" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new ApiClient();
    const values = await Promise.all([
      client.request("/values", valueSchema),
      client.request("/values", valueSchema)
    ]);
    expect(values).toEqual([{ value: 42 }, { value: 42 }]);
    expect(refreshCount).toBe(1);
    expect(fetchMock.mock.calls.every(([, init]) => init.credentials === "include")).toBe(true);
  });

  it("does not refresh a rejected login request", async () => {
    const fetchMock = vi.fn(async () => json({ detail: "Invalid credentials" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    await expect(new ApiClient().request("/auth/login", z.unknown(), { method: "POST" }, false))
      .rejects.toMatchObject({ status: 401, message: "Invalid credentials" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not loop when the retried request is still unauthorized", async () => {
    const fetchMock = vi.fn(async (path: string) =>
      path.endsWith("/auth/refresh") ? json(session) : json({ detail: "Unauthorized" }, 401));
    vi.stubGlobal("fetch", fetchMock);
    await expect(new ApiClient().request("/values", valueSchema)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("rejects malformed successful responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ value: "not-a-number" })));
    await expect(new ApiClient().request("/values", valueSchema))
      .rejects.toMatchObject({ status: 502 });
  });

  it("keeps a failed logout visible instead of claiming success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Database unavailable" }, 503)));
    await expect(new ApiClient().logout()).rejects.toMatchObject({ status: 503 });
  });

  it("does not restore access tokens after an in-flight session is cleared", async () => {
    let resolveResponse: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveResponse = resolve; })));
    const client = new ApiClient();
    const pending = client.refresh();
    client.clearSession();
    resolveResponse?.(json(session));
    await expect(pending).rejects.toMatchObject({ status: 401 });
  });

  it("surfaces network failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Network failure"); }));
    await expect(new ApiClient().request("/values", valueSchema))
      .rejects.toMatchObject({ status: 0 });
  });

  it("notifies the UI when a refresh session is rejected", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Session expired" }, 401)));
    const client = new ApiClient();
    const listener = vi.fn();
    const unsubscribe = client.onSessionExpired(listener);
    await expect(client.refresh()).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
    client.clearSession();
    expect(listener).toHaveBeenCalledOnce();
  });

  it("sends the readable CSRF cookie on refresh and logout", async () => {
    vi.stubGlobal("document", { cookie: "unrelated=value; csrf_token=csrf%2Dvalue" });
    const fetchMock = vi.fn(async (path: string, init: RequestInit) => {
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-value");
      return path.endsWith("/auth/refresh") ? json(session) : new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new ApiClient();
    await client.refresh();
    await client.logout();
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-value");
    }
  });

  it("rejects malformed CSRF cookies before sending a mutating request", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=%invalid" });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(new ApiClient().refresh()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
