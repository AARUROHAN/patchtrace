import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

const BACKEND_URL = (
  process.env.PATCHTRACE_BACKEND_URL || "http://127.0.0.1:8000"
).replace(/\/+$/, "");

async function proxyToBackend(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const encodedPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const target = `${BACKEND_URL}/api/${encodedPath}${incoming.search}`;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");
  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);

  const method = request.method;
  const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  try {
    const upstream = await fetch(target, {
      method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(115_000),
    });

    const responseHeaders = new Headers();
    for (const name of ["content-type", "cache-control", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const name = error instanceof Error ? error.name : "UnknownError";
    // Do not log request bodies, repository content, or secrets.
    console.error(`[PatchTrace API proxy] ${method} /api/${path.join("/")} failed: ${name}`);
    return Response.json(
      { detail: "The backend request could not be completed by the web proxy. Check Render logs and retry." },
      { status: 502 },
    );
  }
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyToBackend(request, context);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxyToBackend(request, context);
}
