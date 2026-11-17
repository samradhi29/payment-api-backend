
import { NextRequest, NextResponse } from "next/server";

type RouteContext = {
  params: Promise<{
    paymentId: string;
  }>;
};

export async function POST(
  _request: NextRequest,
  context: RouteContext
) {
  const apiBaseUrl = process.env.LEDGERCORE_API_URL;
  const merchantApiKey = process.env.LEDGERCORE_MERCHANT_API_KEY;

  if (!apiBaseUrl || !merchantApiKey) {
    return NextResponse.json(
      { detail: "Payment API server configuration is missing." },
      { status: 500 }
    );
  }

  try {
    const { paymentId } = await context.params;

    const backendUrl = new URL(
      `${apiBaseUrl.replace(/\/+$/, "")}/api/v1/payments/${encodeURIComponent(paymentId)}/gateway-order`
    );

    const response = await fetch(backendUrl, {
      method: "POST",
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
