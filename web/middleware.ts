import { NextRequest, NextResponse } from "next/server";
import {
  verifySession,
  authState,
  SESSION_COOKIE,
  PASSWORD_ENV,
  OPEN_ACCESS_ENV,
} from "./lib/auth";

/**
 * The refusal served when no passphrase is configured.
 *
 * Self-contained markup on purpose: /_next is behind this same gate, so a page
 * that pulled in the stylesheet would render unstyled at exactly the moment an
 * operator needs to read it. It names the variable, because "503" on its own
 * sends someone hunting through logs for a deploy that is not broken, just
 * unconfigured.
 */
const UNCONFIGURED_HTML = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard not configured</title>
<body style="margin:0;background:#0b0d10;color:#e6edf3;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:46rem;margin:0 auto;padding:14vh 24px">
<h1 style="font-size:22px;margin:0 0 10px">This dashboard is not configured</h1>
<p style="color:#8b949e;margin:0 0 16px">
No access passphrase is set, so nothing is being served. These scans are
private and the site will not hand them out until someone is allowed to ask
for them.
</p>
<p style="color:#8b949e;margin:0 0 16px">
Operator: set <code style="background:#161b22;padding:2px 7px;border-radius:5px">${PASSWORD_ENV}</code>
in this deployment's environment variables and redeploy. Customers then sign in
with that passphrase at <code style="background:#161b22;padding:2px 7px;border-radius:5px">/login</code>.
</p>
<p style="color:#8b949e;margin:0;font-size:13px">
Running locally without one: <code style="background:#161b22;padding:2px 7px;border-radius:5px">${OPEN_ACCESS_ENV}=1 npm run dev</code>.
That has no effect on a deployed build.
</p>
</div>`;

// Everything is gated except the login flow and static assets.
//
// The order below is the fix. This used to open with
// `if (!authConfigured()) return NextResponse.next()`, so the ONE state in
// which no session can possibly be valid — no DASHBOARD_PASSWORD — was also
// the state in which every path, /api/download included, was served to
// anybody. A missing environment variable silently published the customer's
// private store. Now the unconfigured state is checked first and refuses.
export async function middleware(req: NextRequest) {
  const state = authState();

  if (state === "unconfigured") {
    // 503, not 403: the request is not the problem, the deployment is. No
    // pass-through list above this — /login and /api/login are refused too,
    // because with no passphrase there is nothing to sign in against and a
    // login form that can only ever fail is a worse answer than the reason.
    return new NextResponse(UNCONFIGURED_HTML, {
      status: 503,
      headers: {
        "content-type": "text/html; charset=utf-8",
        // A cached refusal would outlive the fix it asks for.
        "cache-control": "no-store",
      },
    });
  }

  // `next dev` with the hatch explicitly on. Unreachable in any deployed
  // build — see devOpenAccess() in lib/auth.ts.
  if (state === "open-dev") return NextResponse.next();

  const { pathname, search } = req.nextUrl;
  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/api/login") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  if (await verifySession(req.cookies.get(SESSION_COOKIE)?.value)) {
    return NextResponse.next();
  }

  const url = new URL("/login", req.url);
  url.searchParams.set("next", pathname + search);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
