import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { fetchBlob, isOwnBlobUrl } from "../../../lib/blob";
import { verifySession, authConfigured, SESSION_COOKIE } from "../../../lib/auth";

export const dynamic = "force-dynamic";

// Streams a private blob to an authorised user. The store is private, so this
// route is the only way to get an asset — which is exactly what makes
// "permitted users only" enforceable.
export async function GET(req: NextRequest) {
  if (authConfigured()) {
    const jar = await cookies();
    if (!(await verifySession(jar.get(SESSION_COOKIE)?.value))) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const url = req.nextUrl.searchParams.get("url");
  const name = req.nextUrl.searchParams.get("name") || "download";
  // Previews render in an <img>; forcing an attachment disposition would
  // make the browser download them instead of drawing them.
  const inline = req.nextUrl.searchParams.get("inline") === "1";
  if (!url || !isOwnBlobUrl(url)) {
    return NextResponse.json({ error: "bad url" }, { status: 400 });
  }

  const upstream = await fetchBlob(url);
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { error: `blob ${upstream.status}`, detail: (await upstream.text()).slice(0, 300) },
      { status: 502 },
    );
  }

  // Stream rather than buffer: a 25 MB splat should not sit in function memory.
  return new NextResponse(upstream.body, {
    headers: {
      "content-type": upstream.headers.get("content-type") || "application/octet-stream",
      "content-disposition": inline
        ? `inline; filename="${name.replace(/"/g, "")}"`
        : `attachment; filename="${name.replace(/"/g, "")}"`,
      "cache-control": inline ? "private, max-age=300" : "private, no-store",
    },
  });
}
