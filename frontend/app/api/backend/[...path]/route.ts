import { NextRequest, NextResponse } from "next/server";

// Server-side proxy: the browser never sees BACKEND_API_KEY.
const ALLOWED = [/^stats$/, /^memory$/, /^incidents$/, /^incidents\/\d+$/, /^incidents\/\d+\/(approve|reject)$/];

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED.some((r) => r.test(joined))) return NextResponse.json({ detail: "not allowed" }, { status: 404 });

  const base = process.env.BACKEND_URL;
  if (!base) return NextResponse.json({ detail: "BACKEND_URL not set" }, { status: 500 });
  const res = await fetch(`${base}/${joined}${req.nextUrl.search}`, {
    method: req.method,
    headers: { "X-API-Key": process.env.BACKEND_API_KEY ?? "", "Content-Type": "application/json" },
    body: req.method === "POST" ? await req.text() : undefined,
    cache: "no-store",
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") ?? "application/json" },
  });
}
export { forward as GET, forward as POST };
