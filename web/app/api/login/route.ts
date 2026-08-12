import { NextRequest, NextResponse } from "next/server";
import { checkPassword, mintSession, authConfigured } from "../../../lib/auth";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!authConfigured()) {
    return NextResponse.json({ error: "auth not configured" }, { status: 500 });
  }
  const form = await req.formData();
  const password = String(form.get("password") || "");
  const next = String(form.get("next") || "/");

  if (!(await checkPassword(password))) {
    return NextResponse.redirect(new URL("/login?e=1", req.url), { status: 303 });
  }
  const { name, value, maxAge } = await mintSession();
  // Only ever redirect to a path on this site — an absolute `next` would make
  // this an open redirect.
  const dest = next.startsWith("/") && !next.startsWith("//") ? next : "/";
  const res = NextResponse.redirect(new URL(dest, req.url), { status: 303 });
  res.cookies.set(name, value, {
    httpOnly: true, secure: true, sameSite: "lax", path: "/", maxAge,
  });
  return res;
}
