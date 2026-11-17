
import { NextRequest, NextResponse } from "next/server";

type RouteContext = {
  params: Promise<{
    paymentId: string;
  }>;
};

type VerificationRequest = {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
};

export async function POST(
  request: NextRequest,
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

  let body: VerificationRequest | Record<string, never>;

  try {
    const requestBody = await request.json();

    if (
      requestBody === null ||
      typeof requestBody !== "object" ||
      Array.isArray(requestBody)
    ) {
      return NextResponse.json(
        { detail: "Invalid verification request body." },
        { status: 400 }
      );
    }

    const hasRazorpayFields =
      "razorpay_order_id" in requestBody ||
      "razorpay_payment_id" in requestBody ||
      "razorpay_signature" in requestBody;

    if (hasRazorpayFields) {
      const razorpayBody = requestBody as Partial<VerificationRequest>;

      if (
        typeof razorpayBody.razorpay_order_id !== "string" ||
        typeof razorpayBody.razorpay_payment_id !== "string" ||
        typeof razorpayBody.razorpay_signature !== "string" ||
        !razorpayBody.razorpay_order_id.trim() ||
        !razorpayBody.razorpay_payment_id.trim() ||
        !razorpayBody.razorpay_signature.trim()
      ) {
        return NextResponse.json(
          { detail: "All Razorpay verification fields are required." },
          { status: 400 }
        );
      }

      body = {
        razorpay_order_id: razorpayBody.razorpay_order_id,
        razorpay_payment_id: razorpayBody.razorpay_payment_id,
        razorpay_signature: razorpayBody.razorpay_signature,
      };
    } else {
      // Cashfree verification uses the stored provider order ID.
      body = {};
    }
  } catch {
    return NextResponse.json(
      { detail: "Invalid JSON request body." },
      { status: 400 }
    );
  }

  try {
    const { paymentId } = await context.params;

    const normalizedApiBaseUrl = apiBaseUrl.replace(/\/+$/, "");

    const backendUrl = new URL(
      `${normalizedApiBaseUrl}/api/v1/payments/${encodeURIComponent(paymentId)}/verify`
    );

    const response = await fetch(backendUrl, {
      method: "POST",
      headers: {
        "X-Merchant-API-Key": merchantApiKey,
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
