export const dynamic = "force-dynamic";

export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; e?: string }>;
}) {
  const { next = "/", e } = await searchParams;
  return (
    <div className="wrap" style={{ maxWidth: 420, paddingTop: 90 }}>
      {/* The literal front door — the first screen anyone sees, and it was the
          one place still called "Scan assets". */}
      <h1 style={{ marginBottom: 8 }}>
        CrateScanner <span className="qual">(Mesh &amp; 3DGS)</span>
      </h1>
      <p className="muted" style={{ marginBottom: 22 }}>
        Enter the passphrase you were given.
      </p>
      <form method="post" action="/api/login">
        <input type="hidden" name="next" value={next} />
        <input
          type="password"
          name="password"
          placeholder="Access passphrase"
          autoFocus
          required
          style={{
            width: "100%", padding: "11px 13px", borderRadius: 8,
            border: "1px solid var(--line2)", background: "#0e1218",
            color: "var(--fg)", fontSize: 15,
          }}
        />
        {e && (
          <p style={{ color: "var(--bad)", fontSize: 13, marginTop: 10 }}>
            That passphrase is not correct.
          </p>
        )}
        <button
          type="submit"
          className="dl"
          style={{ marginTop: 16, width: "100%", justifyContent: "center",
                   padding: "11px 13px", cursor: "pointer" }}
        >
          Sign in
        </button>
      </form>
    </div>
  );
}
