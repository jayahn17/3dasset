// Server-side access to a PRIVATE Vercel Blob store.
//
// Private-store URLs (…private.blob.vercel-storage.com) return 403 to the
// browser. Every read therefore happens here, on the server, with the
// read-write token that Vercel injects into the deployment — the token is
// never sent to the client and no asset URL is ever directly usable.

export function blobToken(): string {
  return process.env.BLOB_READ_WRITE_TOKEN || "";
}

/**
 * Fetch a blob URL with store credentials attached.
 *
 * `bypassCache` adds `?cache=0`, which is what the official SDK sends for
 * `get(..., {useCache: false})` on a private store. Next's own `no-store` only
 * stops *this app* from caching; the blob CDN can still hand back a previous
 * version of an overwritten pathname. Anything republished in place under a
 * stable name — i.e. the manifest — has to bypass it, or a fresh publish is
 * invisible until the edge entry expires.
 */
export async function fetchBlob(
  url: string,
  init?: RequestInit,
  bypassCache = false,
): Promise<Response> {
  const token = blobToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("authorization", `Bearer ${token}`);
  let target = url;
  if (bypassCache) {
    const u = new URL(url);
    u.searchParams.set("cache", "0");
    target = u.toString();
  }
  return fetch(target, { ...init, headers, cache: "no-store" });
}

/**
 * Only allow proxying URLs that belong to our own blob store — otherwise
 * `/api/download?url=…` is an open proxy anyone can point at any host (SSRF).
 */
export function isOwnBlobUrl(raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (u.protocol !== "https:") return false;
  return u.hostname.endsWith(".blob.vercel-storage.com");
}
