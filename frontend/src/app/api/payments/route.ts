
import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const apiBaseUrl = process.env.LEDGERCORE_API_URL;
  const merchantApiKey = process.env.LEDGERCORE_MERCHANT_API_KEY;

  if (!apiBaseUrl || !merchantApiKey) {
    return NextResponse.json(
      { detail: "Payment API server configuration is missing." },
      { status: 500 }
    );
  }

  const limit = request.nextUrl.searchParams.get("limit") ?? "20";
  const offset = request.nextUrl.searchParams.get("offset") ?? "0";

  const backendUrl = new URL(
    `${apiBaseUrl.replace(/\/+$/, "")}/api/v1/payments`
  );

  backendUrl.searchParams.set("limit", limit);
  backendUrl.searchParams.set("offset", offset);

  try {
    const response = await fetch(backendUrl, {
      method: "GET",
      headers: {
        "X-Merchant-API-Key": merchantApiKey,
        Accept: "application/json",
      },
      cache: "no-store",
    });

    const data = await response.json();

    return NextResponse.json(data, {
      status: response.status,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the payment backend." },
      { status: 502 }
    );
  }
}

export async function POST(request: NextRequest) {
  const apiBaseUrl = process.env.LEDGERCORE_API_URL;
  const merchantApiKey = process.env.LEDGERCORE_MERCHANT_API_KEY;

  if (!apiBaseUrl || !merchantApiKey) {
    return NextResponse.json(
      { detail: "Payment API server configuration is missing." },
      { status: 500 }
    );
  }

  const idempotencyKey = request.headers.get("Idempotency-Key");

  if (!idempotencyKey) {
    return NextResponse.json(
      { detail: "Idempotency-Key header is required." },
      { status: 400 }
    );
  }

  let body: unknown;

  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { detail: "Invalid JSON request body." },
      { status: 400 }
    );
  }

  try {
    const backendUrl = new URL(
      `${apiBaseUrl.replace(/\/+$/, "")}/api/v1/payments`
    );

    const response = await fetch(backendUrl, {
      method: "POST",
      headers: {
        "X-Merchant-API-Key": merchantApiKey,
        "Idempotency-Key": idempotencyKey,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });

    const data = await response.json();

    return NextResponse.json(data, {
      status: response.status,
    });
  } catch {
    return NextResponse.json(
      { detail: "Unable to connect to the payment backend." },
      { status: 502 }
    );
  }
}
