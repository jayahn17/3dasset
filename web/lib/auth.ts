// Access gate for the dashboard.
//
// The blob store is PRIVATE, so asset URLs are not reachable on their own.
// Everything is served through this app, and this decides who gets in.
//
// A shared passphrase is deliberate: the customer set is small and known, and
// a real IdP is a much larger commitment. The cookie is an HMAC over an expiry
// so it cannot be forged or replayed past its lifetime, and nothing derived
// from the passphrase is ever sent to the browser.
//
// THE TRAP THIS FILE EXISTS TO CLOSE
// ----------------------------------
// This used to answer one question — "is a password set?" — and every caller
// treated a `false` as "then let everyone through". middleware.ts opened with
// `if (!authConfigured()) return NextResponse.next()` and app/api/download
// skipped its session check on the same predicate, so a deployment that simply
// never had DASHBOARD_PASSWORD added served the entire private store to the
// open internet, using the deployment's own BLOB_READ_WRITE_TOKEN to do it.
// That is not a hypothetical: `vercel env pull` into web/.env.local produces
// NEXT_PUBLIC_MANIFEST_URL and BLOB_READ_WRITE_TOKEN and no DASHBOARD_PASSWORD.
// A forgotten variable must never be the thing that publishes a customer's
// scans, so the missing-password state now REFUSES instead of waving through.

const COOKIE = "dash_session";
const MAX_AGE_S = 60 * 60 * 12; // 12h

/** The variable an operator has to set. Exported so the refusal can name it. */
export const PASSWORD_ENV = "DASHBOARD_PASSWORD";
/** The dev-only escape hatch. Exported for the same reason. */
export const OPEN_ACCESS_ENV = "DASHBOARD_ALLOW_OPEN_ACCESS";

/**
 * What the gate is doing right now.
 *
 *   "enforced"     a passphrase is set; require a valid session. Normal.
 *   "unconfigured" no passphrase and no dev hatch. Serve NOTHING and say why.
 *   "open-dev"     `next dev` with the hatch explicitly on. Serve everything.
 *
 * Ordered so that the safe state is what you get by doing nothing.
 */
export type AuthState = "enforced" | "unconfigured" | "open-dev";

/**
 * The only way to run with no passphrase, and it cannot be reached by omission.
 *
 * Two independent locks, both of which must be deliberately set to the right
 * value: the operator has to opt in by name, AND the build has to be a dev
 * build. NODE_ENV is tested POSITIVELY against "development" rather than
 * negatively against "production" because `next build` — and therefore every
 * Vercel deployment, preview included — sets it to "production". So the hatch
 * is off in production even if someone copies DASHBOARD_ALLOW_OPEN_ACCESS=1
 * into the project's environment variables, which is the realistic accident.
 */
function devOpenAccess(): boolean {
  return (
    process.env.NODE_ENV === "development" &&
    process.env[OPEN_ACCESS_ENV] === "1"
  );
}

export function authState(): AuthState {
  if (process.env[PASSWORD_ENV]) return "enforced";
  if (devOpenAccess()) return "open-dev";
  return "unconfigured";
}

/**
 * Must this request prove it is authorised?
 *
 * READ THE NAME AS A QUESTION ABOUT THE REQUEST, NOT ABOUT THE ENVIRONMENT.
 * It is true in the "unconfigured" state — where no password exists and no
 * session can ever be valid — precisely so that a caller written as
 *
 *     if (authConfigured()) { ...require a session... }
 *
 * denies instead of serving when the deployment is misconfigured. That is the
 * whole fix: app/api/download/route.ts and app/api/login/route.ts are owned
 * elsewhere and both guard on this predicate, and both become fail-closed
 * without an edit. Inverting this to mean "a password exists" re-opens the
 * store; if the name is ever changed, change the meaning nowhere.
 */
export function authConfigured(): boolean {
  return authState() !== "open-dev";
}

function secret(): string {
  // AUTH_SECRET signs cookies; DASHBOARD_PASSWORD is what users type.
  return process.env.AUTH_SECRET || process.env[PASSWORD_ENV] || "";
}

async function hmac(data: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return Buffer.from(new Uint8Array(sig)).toString("base64url");
}

/** Constant-time compare — a plain === leaks the answer through timing. */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function checkPassword(input: string): Promise<boolean> {
  const expected = process.env[PASSWORD_ENV] || "";
  if (!expected) return false;
  // Hash both sides first so the compare is over fixed-length strings.
  return timingSafeEqual(await hmac(input), await hmac(expected));
}

export async function mintSession(): Promise<{ name: string; value: string; maxAge: number }> {
  // Unreachable through /api/login, which checks the passphrase first — but a
  // cookie signed with an empty key is a credential anyone can mint, so this
  // refuses to produce one rather than trusting its only caller to stay correct.
  if (!secret()) throw new Error(`cannot mint a session: ${PASSWORD_ENV} is not set`);
  const expires = Date.now() + MAX_AGE_S * 1000;
  const value = `${expires}.${await hmac(String(expires))}`;
  return { name: COOKIE, value, maxAge: MAX_AGE_S };
}

export async function verifySession(value: string | undefined): Promise<boolean> {
  // With neither AUTH_SECRET nor DASHBOARD_PASSWORD set, hmac() keys HMAC with
  // the empty string — a key an attacker also has, so a self-signed
  // "<expiry>.<hmac>" cookie would verify. The gate refuses this state at the
  // door, but /api/download reaches verifySession directly, so the check is
  // repeated at the point where the answer is actually used.
  if (!secret()) return false;
  if (!value) return false;
  const [expiresRaw, sig] = value.split(".");
  const expires = Number(expiresRaw);
  if (!expires || !sig || Date.now() > expires) return false;
  return timingSafeEqual(sig, await hmac(String(expires)));
}

export const SESSION_COOKIE = COOKIE;
