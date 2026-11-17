
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
    vus: 5,
    duration: "30s",
    thresholds: {
        http_req_failed: ["rate<0.01"],
        checks: ["rate>0.99"],
    },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const MERCHANT_API_KEY = __ENV.MERCHANT_API_KEY;
const CUSTOMER_ACCOUNT_ID = __ENV.CUSTOMER_ACCOUNT_ID;

export default function () {
    if (!MERCHANT_API_KEY || !CUSTOMER_ACCOUNT_ID) {
        throw new Error(
            "MERCHANT_API_KEY and CUSTOMER_ACCOUNT_ID environment variables are required."
        );
    }

    const payload = JSON.stringify({
        customer_account_id: CUSTOMER_ACCOUNT_ID,
        amount_minor: 100,
        currency: "INR",
    });

    const headers = {
        "Content-Type": "application/json",
        "X-Merchant-API-Key": MERCHANT_API_KEY,
        "Idempotency-Key": `k6-${__VU}-${__ITER}-${Date.now()}`,
    };

    const response = http.post(
        `${BASE_URL}/api/v1/payments`,
        payload,
        { headers }
    );

    check(response, {
        "payment created (HTTP 201)": (r) => r.status === 201,
        "response contains payment ID": (r) => {
            try {
                return Boolean(r.json("id"));
            } catch {
                return false;
            }
        },
    });

    sleep(1);
}
