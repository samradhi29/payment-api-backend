"use client";
import Script from "next/script";
import {
     useCallback,
     useEffect,
     useMemo,
     useRef,
     useState,
} from "react";
type PaymentStatus =
     | "pending"
     | "processing"
     | "succeeded"
     | "failed"
     | "needs_review";
type Payment = {
     id: string;
     customer_account_id: string;
     merchant_account_id: string;
     amount_minor: number;
     currency: string;
     provider: "razorpay" | "cashfree" | "payu" | "auto";
     status: PaymentStatus;
     created_at: string;
};
type GatewayOrder = {
     payment_id: string;
     provider: string;
     provider_order_id: string;
     amount_minor: number;
     currency: string;
     key_id?: string;
     payment_session_id?: string;
     environment?: string;
     gateway_status?: string;
     checkout_url?: string;
     method?: string;
     fields?: Record<string, string>;
     checkout_data?: { payment_url?: string; checkout_url?: string; fields?: Record<string, string> };
};
type RazorpayCheckoutResponse = {
     razorpay_order_id: string;
     razorpay_payment_id: string;
     razorpay_signature: string;
};
type RazorpayOptions = {
     key: string;
     amount: number;
     currency: string;
     name: string;
     description: string;
     order_id: string;
     theme?: {
          color: string;
     };
     modal?: {
          ondismiss?: () => void;
     };
     handler: (response: RazorpayCheckoutResponse) => void;
};
type CashfreeCheckout = (options: {
     paymentSessionId: string;
     redirectTarget?: "_modal" | "_self";
}) => Promise<unknown>;

type CashfreeInstance = {
     checkout: CashfreeCheckout;
};

type CashfreeFactory = (options: { mode: "sandbox" | "production" }) => CashfreeInstance;

type RazorpayInstance = {
     open: () => void;
     on: (
          event: "payment.failed",
          handler: (response: {
               error?: {
                    description?: string;
                    reason?: string;
               };
          }) => void
     ) => void;
};
declare global {
     interface Window {
          Razorpay?: new (options: RazorpayOptions) => RazorpayInstance;
          Cashfree?: CashfreeFactory;
     }
}
const filters = [
     "All",
     "Succeeded",
     "Pending",
     "Processing",
     "Failed",
     "Needs review",
];
const DEFAULT_CUSTOMER_ID =
     "16a4fda6-b4c8-4135-a910-da5fbffbc198";
function formatAmount(amountMinor: number, currency: string) {
     return new Intl.NumberFormat("en-IN", {
          style: "currency",
          currency,
          maximumFractionDigits: 2,
     }).format(amountMinor / 100);
}
function formatDate(date: string) {
     return new Intl.DateTimeFormat("en-IN", {
          dateStyle: "medium",
          timeStyle: "short",
     }).format(new Date(date));
}
function displayStatus(status: PaymentStatus) {
     return status.replace("_", " ").replace(/\b\w/g, (letter) =>
          letter.toUpperCase()
     );
}
function statusColor(status: PaymentStatus) {
     switch (status) {
          case "succeeded":
               return "bg-emerald-500/10 text-emerald-400";
          case "pending":
          case "processing":
               return "bg-amber-500/10 text-amber-400";
          case "failed":
               return "bg-rose-500/10 text-rose-400";
          case "needs_review":
               return "bg-orange-500/10 text-orange-400";
          default:
               return "bg-slate-500/10 text-slate-400";
     }
}
function shortId(id: string) {
     return `${id.slice(0, 8)}...${id.slice(-4)}`;
}
export default function Home() {
     const [payments, setPayments] = useState<Payment[]>([]);
     const [activeFilter, setActiveFilter] = useState("All");
     const [search, setSearch] = useState("");
     const [showModal, setShowModal] = useState(false);
     const [activePage, setActivePage] = useState("Overview");
     const [loading, setLoading] = useState(true);
     const [error, setError] = useState("");
     const [refreshing, setRefreshing] = useState(false);
     const [customerAccountId, setCustomerAccountId] =
          useState(DEFAULT_CUSTOMER_ID);
     const [amountRupees, setAmountRupees] = useState("100.00");
     const [creating, setCreating] = useState(false);
     const [createError, setCreateError] = useState("");
     const [notice, setNotice] = useState("");
     const [razorpayLoaded, setRazorpayLoaded] = useState(false);
     const [cashfreeLoaded, setCashfreeLoaded] = useState(false);
     const [selectedProvider, setSelectedProvider] = useState<"razorpay" | "cashfree" | "payu" | "auto">("razorpay");
     const [customerPhone, setCustomerPhone] = useState("");
     const [customerEmail, setCustomerEmail] = useState("");
     const [checkoutPaymentId, setCheckoutPaymentId] = useState<string | null>(
          null
     );
     const [checkoutError, setCheckoutError] = useState("");
     const idempotencyKeyRef = useRef<string | null>(null);
     const fetchPayments = useCallback(async () => {
          try {
               const response = await fetch("/api/payments?limit=20&offset=0", {
                    method: "GET",
                    cache: "no-store",
               });
               const responseData = await response.json().catch(() => null);
               if (!response.ok) {
                    throw new Error(
                         responseData?.detail ||
                              `Unable to load payments (${response.status}).`
                    );
               }
               const records = Array.isArray(responseData)
                    ? responseData
                    : responseData.items ??
                         responseData.value ??
                         responseData.payments ??
                         [];
               if (!Array.isArray(records)) {
                    throw new Error("The payment API returned an unexpected response.");
               }
               setError("");
               setPayments(records);
          } catch (err) {
               setError(
                    err instanceof Error
                         ? err.message
                         : "Something went wrong while loading payments."
               );
          } finally {
               setLoading(false);
               setRefreshing(false);
          }
     }, []);
     const refreshPayments = useCallback(() => {
          setRefreshing(true);
          void fetchPayments();
     }, [fetchPayments]);
     // Initial payment loading.
     // State updates happen after the fetch promise resolves.
     useEffect(() => {
          let cancelled = false;
          fetch("/api/payments?limit=20&offset=0", {
               method: "GET",
               cache: "no-store",
          })
               .then(async (response) => {
                    const responseData = await response.json().catch(() => null);
                    if (!response.ok) {
                         throw new Error(
                              responseData?.detail ||
                                   `Unable to load payments (${response.status}).`
                         );
                    }
                    const records = Array.isArray(responseData)
                         ? responseData
                         : responseData.items ??
                              responseData.value ??
                              responseData.payments ??
                              [];
                    if (!Array.isArray(records)) {
                         throw new Error("The payment API returned an unexpected response.");
                    }
                    return records as Payment[];
               })
               .then((records) => {
                    if (!cancelled) {
                         setError("");
                         setPayments(records);
                    }
               })
               .catch((err: unknown) => {
                    if (!cancelled) {
                         setError(
                              err instanceof Error
                                   ? err.message
                                   : "Something went wrong while loading payments."
                         );
                    }
               })
               .finally(() => {
                    if (!cancelled) {
                         setLoading(false);
                    }
               });
          return () => {
               cancelled = true;
          };
     }, []);
     const verifyPayment = async (payment: Payment, verificationBody: Record<string, string> = {}) => {
          setNotice("Payment received. Verifying with the backend...");
          const verificationResponse = await fetch(
               `/api/payments/${encodeURIComponent(payment.id)}/verify`,
               {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(verificationBody),
                    cache: "no-store",
               }
          );
          const verificationData = await verificationResponse.json().catch(() => null);
          if (!verificationResponse.ok) {
               throw new Error(
                    verificationData?.detail ||
                         `Payment verification failed (${verificationResponse.status}).`
               );
          }
          setNotice(
               `Payment successful! Ledger transaction: ${verificationData.ledger_transaction_id}`
          );
          setCheckoutError("");
          refreshPayments();
     };

     // Create a provider order and launch the matching checkout experience.
     const startCheckout = async (payment: Payment) => {
          if (checkoutPaymentId) return;

          const requestedProvider = payment.provider || "auto";
          let checkoutProvider: string = requestedProvider;
          if (!["razorpay", "cashfree", "payu", "auto"].includes(requestedProvider)) {
               setCheckoutError(`${requestedProvider} checkout is not supported.`);
               return;
          }

          setCheckoutError("");
          setNotice("");
          setCheckoutPaymentId(payment.id);

          try {
               const orderResponse = await fetch(
                    `/api/payments/${encodeURIComponent(payment.id)}/gateway-order`,
                    { method: "POST", cache: "no-store" }
               );
               const orderData = await orderResponse.json().catch(() => null);
               if (!orderResponse.ok) {
                    throw new Error(
                         orderData?.detail ||
                              `Unable to create ${requestedProvider} order (${orderResponse.status}).`
                    );
               }

               const order = orderData as GatewayOrder;
               checkoutProvider = (order.provider || requestedProvider).toLowerCase();
               if (!order.provider_order_id || !order.amount_minor || !order.currency) {
                    throw new Error("The backend returned incomplete gateway order details.");
               }

               if (checkoutProvider === "payu") {
                    const checkoutUrl = order.checkout_url || order.checkout_data?.checkout_url || order.checkout_data?.payment_url;
                    const fields = order.fields || order.checkout_data?.fields;
                    if (!checkoutUrl || !["https://test.payu.in/_payment", "https://secure.payu.in/_payment"].includes(checkoutUrl) || !fields || typeof fields !== "object") {
                         throw new Error("PayU returned invalid hosted-checkout details.");
                    }
                    const form = document.createElement("form");
                    form.method = (order.method || "POST").toUpperCase();
                    form.action = checkoutUrl;
                    form.style.display = "none";
                    for (const [name, value] of Object.entries(fields)) {
                         const input = document.createElement("input");
                         input.type = "hidden";
                         input.name = name;
                         input.value = String(value);
                         form.appendChild(input);
                    }
                    document.body.appendChild(form);
                    setNotice("Redirecting to PayU secure checkout...");
                    form.submit();
                    return;
               }

               if (checkoutProvider === "razorpay") {
                    if (!razorpayLoaded || !window.Razorpay) {
                         throw new Error("Razorpay Checkout is still loading. Please try again.");
                    }
                    if (!order.key_id || !window.Razorpay) {
                         throw new Error("Razorpay order is missing its checkout key.");
                    }
                    const razorpay = new window.Razorpay({
                         key: order.key_id,
                         amount: order.amount_minor,
                         currency: order.currency,
                         name: "LedgerCore",
                         description: `Payment ${shortId(payment.id)}`,
                         order_id: order.provider_order_id,
                         theme: { color: "#6366f1" },
                         handler: async (checkoutResponse) => {
                              try {
                                   await verifyPayment(payment, {
                                        razorpay_order_id: checkoutResponse.razorpay_order_id,
                                        razorpay_payment_id: checkoutResponse.razorpay_payment_id,
                                        razorpay_signature: checkoutResponse.razorpay_signature,
                                   });
                              } catch (err) {
                                   setCheckoutError(
                                        err instanceof Error ? err.message : "Payment verification failed."
                                   );
                                   refreshPayments();
                              } finally {
                                   setCheckoutPaymentId(null);
                              }
                         },
                         modal: {
                              ondismiss: () => {
                                   setCheckoutPaymentId(null);
                                   setNotice("Checkout was closed. You can retry from the Payments table.");
                                   refreshPayments();
                              },
                         },
                    });
                    razorpay.on("payment.failed", (response) => {
                         setCheckoutError(
                              response.error?.description ||
                                   response.error?.reason ||
                                   "Razorpay reported that the payment failed."
                         );
                         setCheckoutPaymentId(null);
                         refreshPayments();
                    });
                    razorpay.open();
                    return;
               }

               if (checkoutProvider !== "cashfree") {
                    throw new Error(`Unsupported checkout provider: ${checkoutProvider}`);
               }
               if (!cashfreeLoaded || !order.payment_session_id || !window.Cashfree) {
                    throw new Error("Cashfree checkout is still loading or the payment session is missing.");
               }
               const cashfree = window.Cashfree({
                    mode: order.environment === "production" ? "production" : "sandbox",
               });
               await cashfree.checkout({
                    paymentSessionId: order.payment_session_id,
                    redirectTarget: "_modal",
               });
               await verifyPayment(payment);
               setCheckoutPaymentId(null);
          } catch (err) {
               setCheckoutError(
                    err instanceof Error ? err.message : `Unable to start ${checkoutProvider} Checkout.`
               );
               setCheckoutPaymentId(null);
               refreshPayments();
          }
     };

     const handleCreatePayment = async (
          event: React.FormEvent<HTMLFormElement>
     ) => {
          event.preventDefault();

          // Read the provider directly from the submitted form. This ensures
          // the request uses the exact option currently selected in the UI.
          const formData = new FormData(event.currentTarget);
          const providerValue = formData.get("provider");
          if (providerValue !== "razorpay" && providerValue !== "cashfree" && providerValue !== "payu" && providerValue !== "auto") {
               setCreateError("Please select a valid payment gateway.");
               return;
          }
          const provider = providerValue;

          setCreateError("");
          setCheckoutError("");
          setNotice("");
          if (provider === "razorpay" && (!razorpayLoaded || !window.Razorpay)) {
               setCreateError("Razorpay Checkout is still loading. Please try again.");
               return;
          }
          if (provider === "cashfree" && (!cashfreeLoaded || !window.Cashfree)) {
               setCreateError("Cashfree Checkout is still loading. Please try again.");
               return;
          }
          const normalizedAmount = amountRupees.trim();
          if (!/^\d+(\.\d{1,2})?$/.test(normalizedAmount)) {
               setCreateError(
                    "Enter a valid amount with no more than two decimal places."
               );
               return;
          }
          const amount = Number(normalizedAmount);
          const amountMinor = Math.round(amount * 100);
          if (!Number.isSafeInteger(amountMinor) || amountMinor <= 0) {
               setCreateError("Enter an amount greater than zero.");
               return;
          }
          if (!customerAccountId.trim()) {
               setCreateError("Customer account ID is required.");
               return;
          }
          if (provider === "cashfree" || provider === "payu") {
               if (!/^\+?[1-9]\d{9,14}$/.test(customerPhone.trim())) {
                    setCreateError("Enter a valid phone number with country code, e.g. +919876543210.");
                    return;
               }
          }
          if (provider === "payu" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(customerEmail.trim())) {
               setCreateError("Enter a valid customer email address for PayU.");
               return;
          }
          if (provider === "cashfree" && amountMinor < 100) {
               setCreateError("Cashfree payments must be at least ₹1.");
               return;
          }
          if (!idempotencyKeyRef.current) {
               idempotencyKeyRef.current = crypto.randomUUID();
          }
          setCreating(true);
          try {
               const response = await fetch("/api/payments", {
                    method: "POST",
                    headers: {
                         "Content-Type": "application/json",
                         "Idempotency-Key": idempotencyKeyRef.current,
                    },
                    body: JSON.stringify({
                         customer_account_id: customerAccountId.trim(),
                         amount_minor: amountMinor,
                         currency: "INR",
                         provider,
                         ...(customerPhone.trim() && (provider === "cashfree" || provider === "payu" || provider === "auto") ? { customer_phone: customerPhone.trim() } : {}),
                         ...(customerEmail.trim() && (provider === "payu" || provider === "auto") ? { customer_email: customerEmail.trim() } : {}),
                    }),
               });
               const data = await response.json().catch(() => null);
               if (!response.ok) {
                    throw new Error(
                         data?.detail ||
                              `Payment creation failed with status ${response.status}.`
                    );
               }
               const createdPayment = data as Payment;
               idempotencyKeyRef.current = null;
               setShowModal(false);
               setAmountRupees("100.00");
               setCustomerPhone("");
               setCustomerEmail("");
               setSelectedProvider("razorpay");
               setCreateError("");
               setNotice(
                    `Payment record created: ${createdPayment.id}. Preparing ${createdPayment.provider} Checkout...`
               );
               refreshPayments();
               await startCheckout(createdPayment);
          } catch (err) {
               setCreateError(
                    err instanceof Error
                         ? err.message
                         : "Unable to create payment. Please try again."
               );
          } finally {
               setCreating(false);
          }
     };
     const filteredPayments = useMemo(() => {
          const normalizedSearch = search.toLowerCase().trim();
          return payments.filter((payment) => {
               const matchesFilter =
                    activeFilter === "All" ||
                    displayStatus(payment.status).toLowerCase() ===
                         activeFilter.toLowerCase();
               const matchesSearch =
                    payment.id.toLowerCase().includes(normalizedSearch) ||
                    payment.customer_account_id
                         .toLowerCase()
                         .includes(normalizedSearch);
               return matchesFilter && matchesSearch;
          });
     }, [payments, activeFilter, search]);
     const stats = useMemo(() => {
          const succeeded = payments.filter(
               (payment) => payment.status === "succeeded"
          );
          const failed = payments.filter(
               (payment) =>
                    payment.status === "failed" || payment.status === "needs_review"
          );
          const successfulAmount = succeeded.reduce(
               (total, payment) => total + payment.amount_minor,
               0
          );
          return [
               {
                    label: "Payments on this page",
                    value: payments.length.toLocaleString("en-IN"),
                    description: "Latest API results",
               },
               {
                    label: "Successful payments",
                    value: succeeded.length.toLocaleString("en-IN"),
                    description: "Succeeded transactions",
               },
               {
                    label: "Successful volume",
                    value: formatAmount(successfulAmount, "INR"),
                    description: "Successful payments on this page",
               },
               {
                    label: "Unsuccessful / review",
                    value: failed.length.toLocaleString("en-IN"),
                    description: "Failed or requiring review",
               },
          ];
     }, [payments]);
     const navigation = [
          { name: "Overview", icon: "â—‰" },
          { name: "Payments", icon: "â†—" },
          { name: "Customers", icon: "â™™" },
          { name: "Webhooks", icon: "âŒ˜" },
          { name: "API Keys", icon: "âš¿" },
     ];
     return (
          <>
               <Script
                    src="https://checkout.razorpay.com/v1/checkout.js"
                    strategy="afterInteractive"
                    onLoad={() => setRazorpayLoaded(true)}
                    onError={() => {
                         setCheckoutError(
                              "Unable to load Razorpay Checkout. Please check your internet connection and refresh."
                         );
                    }}
               />
               <Script
                    src="https://sdk.cashfree.com/js/v3/cashfree.js"
                    strategy="afterInteractive"
                    onLoad={() => setCashfreeLoaded(true)}
                    onError={() => setCheckoutError("Unable to load Cashfree Checkout SDK.")}
               />
               <div className="min-h-screen bg-[#090d18] text-slate-100">
                    <div className="flex min-h-screen">
                         <aside className="hidden w-64 shrink-0 border-r border-white/10 bg-[#0d1220] p-6 md:flex md:flex-col">
                              <div className="mb-12 flex items-center gap-3">
                                   <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-500 text-xl font-bold">
                                        L
                                   </div>
                                   <div>
                                        <h1 className="text-lg font-bold tracking-wide">
                                             LedgerCore
                                        </h1>
                                        <p className="text-xs text-slate-500">
                                             Payment Infrastructure
                                        </p>
                                   </div>
                              </div>
                              <p className="mb-4 text-xs font-semibold uppercase tracking-widest text-slate-500">
                                   Workspace
                              </p>
                              <div className="mb-8 rounded-xl border border-white/10 bg-white/5 p-3">
                                   <p className="text-sm font-medium">Merchant Dashboard</p>
                                   <p className="mt-1 text-xs text-slate-500">
                                        Authenticated account
                                   </p>
                              </div>
                              <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
                                   Menu
                              </p>
                              <nav className="space-y-2">
                                   {navigation.map((item) => (
                                        <button
                                             key={item.name}
                                             onClick={() => setActivePage(item.name)}
                                             className={`flex w-full items-center gap-3 rounded-lg px-4 py-3 text-left text-sm transition ${
                                                  activePage === item.name
                                                       ? "bg-indigo-500/15 font-medium text-indigo-400"
                                                       : "text-slate-400 hover:bg-white/5 hover:text-white"
                                             }`}
                                        >
                                             <span className="text-lg">{item.icon}</span>
                                             {item.name}
                                        </button>
                                   ))}
                              </nav>
                              <div className="mt-auto rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                                   <div className="flex items-center gap-2">
                                        <span className="h-2 w-2 rounded-full bg-emerald-400" />
                                        <span className="text-sm font-medium text-emerald-400">
                                             Test environment
                                        </span>
                                   </div>
                                   <p className="mt-2 text-xs leading-5 text-slate-500">
                                        You are using Razorpay test mode. No real payments are
                                        processed.
                                   </p>
                              </div>
                         </aside>
                         <main className="min-w-0 flex-1">
                              <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 px-6 py-5 lg:px-10">
                                   <div>
                                        <p className="text-xs text-slate-500">
                                             Workspace / {activePage}
                                        </p>
                                        <h2 className="mt-1 text-xl font-semibold">{activePage}</h2>
                                   </div>
                                   <div className="flex items-center gap-4">
                                        <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-400">
                                             â— Test mode
                                        </span>
                                        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-500/20 text-sm font-semibold text-indigo-300">
                                             M
                                        </div>
                                   </div>
                              </header>
                              <div className="mx-auto max-w-7xl space-y-8 p-6 lg:p-10">
                                   <section className="flex flex-wrap items-center justify-between gap-4">
                                        <div>
                                             <h3 className="text-2xl font-semibold">Overview</h3>
                                             <p className="mt-1 text-sm text-slate-400">
                                                  Monitor your payment activity and transaction health.
                                             </p>
                                        </div>
                                        <div className="flex flex-wrap gap-3">
                                             <button
                                                  onClick={() => refreshPayments()}
                                                  disabled={loading || refreshing}
                                                  className="rounded-lg border border-white/10 px-4 py-2.5 text-sm text-slate-300 transition hover:bg-white/5 disabled:opacity-50"
                                             >
                                                  {refreshing ? "Refreshing..." : "â†» Refresh"}
                                             </button>
                                             <button
                                                  onClick={() => {
                                                       setCreateError("");
                                                       setCheckoutError("");
                                                       setNotice("");
                                                       setShowModal(true);
                                                  }}
                                                  className="rounded-lg bg-indigo-500 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400"
                                             >
                                                  + Create payment
                                             </button>
                                        </div>
                                   </section>
                                   {notice && (
                                        <section className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                                             <p className="text-sm text-emerald-400">{notice}</p>
                                        </section>
                                   )}
                                   {checkoutError && (
                                        <section className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-4">
                                             <p className="text-sm font-medium text-rose-400">
                                                  Checkout / verification issue
                                             </p>
                                             <p className="mt-1 text-sm text-slate-300">
                                                  {checkoutError}
                                             </p>
                                        </section>
                                   )}
                                   {error && (
                                        <section className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-5">
                                             <p className="font-medium text-rose-400">
                                                  Could not load payments
                                             </p>
                                             <p className="mt-2 text-sm text-slate-400">{error}</p>
                                             <button
                                                  onClick={() => {
                                                       setLoading(true);
                                                       void fetchPayments();
                                                  }}
                                                  className="mt-4 rounded-lg bg-rose-500/10 px-4 py-2 text-sm text-rose-300 hover:bg-rose-500/20"
                                             >
                                                  Try again
                                             </button>
                                        </section>
                                   )}
                                   <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                                        {stats.map((metric) => (
                                             <div
                                                  key={metric.label}
                                                  className="rounded-xl border border-white/10 bg-[#111827] p-5"
                                             >
                                                  <p className="text-sm text-slate-400">{metric.label}</p>
                                                  <p className="mt-4 break-words text-2xl font-semibold tracking-tight">
                                                       {loading ? "..." : metric.value}
                                                  </p>
                                                  <p className="mt-2 text-xs text-slate-500">
                                                       {metric.description}
                                                  </p>
                                             </div>
                                        ))}
                                   </section>
                                   <section className="grid gap-5 xl:grid-cols-3">
                                        <div className="rounded-xl border border-white/10 bg-[#111827] p-6 xl:col-span-2">
                                             <h3 className="font-semibold">Payment activity</h3>
                                             <p className="mt-1 text-xs text-slate-500">
                                                  Live data from your FastAPI payment service
                                             </p>
                                             <div className="mt-8 flex min-h-40 flex-col items-center justify-center rounded-lg border border-dashed border-white/10 px-4 text-center">
                                                  {loading ? (
                                                       <p className="text-sm text-slate-400">
                                                            Loading payment activity...
                                                       </p>
                                                  ) : error ? (
                                                       <p className="text-sm text-rose-400">
                                                            Payment activity is unavailable.
                                                       </p>
                                                  ) : payments.length === 0 ? (
                                                       <>
                                                            <span className="text-3xl text-slate-600">â†—</span>
                                                            <p className="mt-3 font-medium text-slate-300">
                                                                 No payment activity yet
                                                            </p>
                                                            <p className="mt-1 text-sm text-slate-500">
                                                                 Real payment activity will appear here after your
                                                                 first transaction.
                                                            </p>
                                                       </>
                                                  ) : (
                                                       <>
                                                            <p className="text-3xl font-semibold">
                                                                 {payments.length}
                                                            </p>
                                                            <p className="mt-2 text-sm text-slate-400">
                                                                 Payments returned by the API
                                                            </p>
                                                            <p className="mt-1 text-xs text-slate-500">
                                                                 Showing the latest 20 records
                                                            </p>
                                                       </>
                                                  )}
                                             </div>
                                        </div>
                                        <div className="rounded-xl border border-white/10 bg-[#111827] p-6">
                                             <h3 className="font-semibold">Transaction status</h3>
                                             <p className="mt-1 text-xs text-slate-500">
                                                  Current page distribution
                                             </p>
                                             <div className="mt-8 space-y-5">
                                                  {[
                                                       {
                                                            label: "Succeeded",
                                                            status: "succeeded",
                                                            color: "bg-emerald-400",
                                                       },
                                                       {
                                                            label: "Pending / Processing",
                                                            status: "pending",
                                                            color: "bg-amber-400",
                                                       },
                                                       {
                                                            label: "Failed",
                                                            status: "failed",
                                                            color: "bg-rose-400",
                                                       },
                                                       {
                                                            label: "Needs review",
                                                            status: "needs_review",
                                                            color: "bg-orange-400",
                                                       },
                                                  ].map((item) => {
                                                       const count =
                                                            item.status === "pending"
                                                                 ? payments.filter(
                                                                           (payment) =>
                                                                                payment.status === "pending" ||
                                                                                payment.status === "processing"
                                                                      ).length
                                                                 : payments.filter(
                                                                           (payment) => payment.status === item.status
                                                                      ).length;
                                                       const percentage =
                                                            payments.length > 0
                                                                 ? (count / payments.length) * 100
                                                                 : 0;
                                                       return (
                                                            <div key={item.label}>
                                                                 <div className="mb-2 flex justify-between text-sm">
                                                                      <span className="text-slate-400">
                                                                           {item.label}
                                                                      </span>
                                                                      <span>{loading ? "..." : count}</span>
                                                                 </div>
                                                                 <div className="h-2 overflow-hidden rounded-full bg-white/5">
                                                                      <div
                                                                           className={`h-full rounded-full ${item.color}`}
                                                                           style={{ width: `${percentage}%` }}
                                                                      />
                                                                 </div>
                                                            </div>
                                                       );
                                                  })}
                                             </div>
                                             <p className="mt-6 text-xs text-slate-500">
                                                  Percentages are based on the currently loaded payments
                                                  only.
                                             </p>
                                        </div>
                                   </section>
                                   <section className="overflow-hidden rounded-xl border border-white/10 bg-[#111827]">
                                        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 p-6">
                                             <div>
                                                  <h3 className="font-semibold">Recent payments</h3>
                                                  <p className="mt-1 text-xs text-slate-500">
                                                       Actual transactions retrieved from your backend
                                                  </p>
                                             </div>
                                             <input
                                                  value={search}
                                                  onChange={(event) => setSearch(event.target.value)}
                                                  placeholder="Search payment or customer ID..."
                                                  className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-2 text-sm outline-none placeholder:text-slate-600 focus:border-indigo-500 sm:w-72"
                                             />
                                        </div>
                                        <div className="flex gap-5 overflow-x-auto border-b border-white/10 px-6">
                                             {filters.map((filter) => (
                                                  <button
                                                       key={filter}
                                                       onClick={() => setActiveFilter(filter)}
                                                       className={`whitespace-nowrap border-b-2 py-4 text-sm ${
                                                            activeFilter === filter
                                                                 ? "border-indigo-400 text-indigo-400"
                                                                 : "border-transparent text-slate-500 hover:text-white"
                                                       }`}
                                                  >
                                                       {filter}
                                                  </button>
                                             ))}
                                        </div>
                                        <div className="overflow-x-auto">
                                             <table className="w-full text-left text-sm">
                                                  <thead className="bg-white/[0.02] text-xs uppercase tracking-wider text-slate-500">
                                                       <tr>
                                                            <th className="px-6 py-4 font-medium">Payment ID</th>
                                                            <th className="px-6 py-4 font-medium">
                                                                 Customer Account
                                                            </th>
                                                            <th className="px-6 py-4 font-medium">Date</th>
                                                            <th className="px-6 py-4 font-medium">Amount</th>
                                                            <th className="px-6 py-4 font-medium">Status</th>
                                                            <th className="px-6 py-4 font-medium">Action</th>
                                                       </tr>
                                                  </thead>
                                                  <tbody className="divide-y divide-white/5">
                                                       {loading ? (
                                                            <tr>
                                                                 <td
                                                                      colSpan={6}
                                                                      className="px-6 py-12 text-center text-slate-500"
                                                                 >
                                                                      Loading payments from LedgerCore...
                                                                 </td>
                                                            </tr>
                                                       ) : !error && filteredPayments.length > 0 ? (
                                                            filteredPayments.map((payment) => (
                                                                 <tr
                                                                      key={payment.id}
                                                                      className="transition hover:bg-white/[0.02]"
                                                                 >
                                                                      <td className="px-6 py-4 font-mono text-xs text-indigo-300">
                                                                           {shortId(payment.id)}
                                                                      </td>
                                                                      <td className="px-6 py-4 font-mono text-xs text-slate-300">
                                                                           {shortId(payment.customer_account_id)}
                                                                      </td>
                                                                      <td className="px-6 py-4 text-slate-400">
                                                                           {formatDate(payment.created_at)}
                                                                      </td>
                                                                      <td className="px-6 py-4 font-medium">
                                                                           {formatAmount(
                                                                                payment.amount_minor,
                                                                                payment.currency
                                                                           )}
                                                                      </td>
                                                                      <td className="px-6 py-4">
                                                                           <span
                                                                                className={`rounded-full px-3 py-1 text-xs ${statusColor(
                                                                                     payment.status
                                                                                )}`}
                                                                           >
                                                                                {displayStatus(payment.status)}
                                                                           </span>
                                                                      </td>
                                                                      <td className="px-6 py-4">
                                                                           {payment.status === "pending" ? (
                                                                                <button
                                                                                     onClick={() => void startCheckout(payment)}
                                                                                     disabled={
                                                                                          (payment.provider === "razorpay" && !razorpayLoaded) ||
                                                                                          (payment.provider === "cashfree" && !cashfreeLoaded) ||
                                                                                          checkoutPaymentId !== null
                                                                                     }
                                                                                     className="rounded-lg bg-indigo-500/15 px-3 py-2 text-xs font-medium text-indigo-300 transition hover:bg-indigo-500/25 disabled:cursor-not-allowed disabled:opacity-40"
                                                                                >
                                                                                     {checkoutPaymentId === payment.id
                                                                                          ? "Opening..."
                                                                                          : "Pay"}
                                                                                </button>
                                                                           ) : payment.status === "succeeded" ? (
                                                                                <span className="text-xs text-emerald-400">
                                                                                     Completed
                                                                                </span>
                                                                           ) : (
                                                                                <span className="text-xs text-slate-500">
                                                                                     â€”
                                                                                </span>
                                                                           )}
                                                                      </td>
                                                                 </tr>
                                                            ))
                                                       ) : (
                                                            <tr>
                                                                 <td
                                                                      colSpan={6}
                                                                      className="px-6 py-12 text-center text-slate-500"
                                                                 >
                                                                      {error
                                                                           ? "Payments could not be displayed."
                                                                           : payments.length === 0
                                                                                ? "No payments found. Your real transactions will appear here."
                                                                                : "No matching payments found."}
                                                                 </td>
                                                            </tr>
                                                       )}
                                                  </tbody>
                                             </table>
                                        </div>
                                        <div className="border-t border-white/10 px-6 py-4 text-xs text-slate-500">
                                             Showing up to 20 payments Â· Data supplied by your
                                             authenticated merchant API
                                        </div>
                                   </section>
                                   <p className="text-center text-xs text-slate-600">
                                        LedgerCore Â· Payment infrastructure dashboard Â· Live backend
                                        data
                                   </p>
                              </div>
                         </main>
                    </div>
                    {showModal && (
                         <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
                              <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#111827] p-6 shadow-2xl">
                                   <div className="flex items-center justify-between">
                                        <h3 className="text-lg font-semibold">Create payment</h3>
                                        <button
                                             onClick={() => setShowModal(false)}
                                             disabled={creating}
                                             className="text-xl text-slate-400 hover:text-white disabled:opacity-50"
                                             aria-label="Close modal"
                                        >
                                             Ã—
                                        </button>
                                   </div>
                                   <p className="mt-2 text-sm text-slate-400">
                                        Create a payment record and continue to the selected gateway checkout.
                                   </p>
                                   <form
                                        onSubmit={handleCreatePayment}
                                        className="mt-6 space-y-5"
                                   >
                                        <div>
                                             <label htmlFor="paymentProvider" className="mb-2 block text-sm font-medium text-slate-300">
                                                  Payment gateway
                                             </label>
                                             <select
                                                  id="paymentProvider"
                                                  name="provider"
                                                  value={selectedProvider}
                                                  onChange={(event) => {
                                                       setSelectedProvider(event.target.value as "razorpay" | "cashfree" | "payu" | "auto");
                                                       idempotencyKeyRef.current = null;
                                                       setCreateError("");
                                                  }}
                                                  className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-3 text-sm text-slate-200 outline-none focus:border-indigo-500"
                                             >
                                                  <option value="auto">Automatic smart routing</option>
                                                  <option value="razorpay">Razorpay (Test)</option>
                                                  <option value="cashfree">Cashfree (Sandbox)</option>
                                                  <option value="payu">PayU (Sandbox)</option>
                                             </select>
                                        </div>
                                        <div>
                                             <label
                                                  htmlFor="customerAccountId"
                                                  className="mb-2 block text-sm font-medium text-slate-300"
                                             >
                                                  Customer account ID
                                             </label>
                                             <input
                                                  id="customerAccountId"
                                                  type="text"
                                                  required
                                                  value={customerAccountId}
                                                  onChange={(event) => {
                                                       setCustomerAccountId(event.target.value);
                                                       idempotencyKeyRef.current = null;
                                                       setCreateError("");
                                                  }}
                                                  className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-3 font-mono text-xs text-slate-200 outline-none focus:border-indigo-500"
                                             />
                                        </div>
                                        {(selectedProvider === "cashfree" || selectedProvider === "payu" || selectedProvider === "auto") && (
                                             <div>
                                                  <label htmlFor="customerPhone" className="mb-2 block text-sm font-medium text-slate-300">Customer phone number</label>
                                                  <input id="customerPhone" type="tel" required={selectedProvider === "cashfree" || selectedProvider === "payu"} value={customerPhone} onChange={(event) => { setCustomerPhone(event.target.value); idempotencyKeyRef.current = null; setCreateError(""); }} placeholder="+919876543210" className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-3 text-sm text-slate-200 outline-none focus:border-indigo-500" />
                                             </div>
                                        )}
                                        {(selectedProvider === "payu" || selectedProvider === "auto") && (
                                             <div>
                                                  <label htmlFor="customerEmail" className="mb-2 block text-sm font-medium text-slate-300">Customer email</label>
                                                  <input id="customerEmail" type="email" required={selectedProvider === "payu"} value={customerEmail} onChange={(event) => { setCustomerEmail(event.target.value); idempotencyKeyRef.current = null; setCreateError(""); }} placeholder="customer@example.com" className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-3 text-sm text-slate-200 outline-none focus:border-indigo-500" />
                                             </div>
                                        )}
                                        <div>
                                             <label
                                                  htmlFor="amountRupees"
                                                  className="mb-2 block text-sm font-medium text-slate-300"
                                             >
                                                  Amount (INR)
                                             </label>
                                             <input
                                                  id="amountRupees"
                                                  type="number"
                                                  min="0.01"
                                                  step="0.01"
                                                  required
                                                  value={amountRupees}
                                                  onChange={(event) => {
                                                       setAmountRupees(event.target.value);
                                                       idempotencyKeyRef.current = null;
                                                       setCreateError("");
                                                  }}
                                                  placeholder="100.00"
                                                  className="w-full rounded-lg border border-white/10 bg-[#090d18] px-4 py-3 text-sm text-slate-200 outline-none focus:border-indigo-500"
                                             />
                                             <p className="mt-2 text-xs text-slate-500">
                                                  The amount will be converted into paise before being
                                                  sent to the API.
                                             </p>
                                        </div>
                                        {createError && (
                                             <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 p-3 text-sm text-rose-400">
                                                  {createError}
                                             </div>
                                        )}
                                        <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs leading-5 text-amber-300">
                                             This creates a pending payment record first. The selected gateway
                                             handles checkout. Your ledger
                                             is updated only after backend verification.
                                        </div>
                                        {selectedProvider === "razorpay" && !razorpayLoaded && (
                                             <p className="text-xs text-amber-300">Loading Razorpay Checkout...</p>
                                        )}
                                        {selectedProvider === "cashfree" && !cashfreeLoaded && (
                                             <p className="text-xs text-amber-300">Loading Cashfree Checkout...</p>
                                        )}
                                        <div className="flex gap-3">
                                             <button
                                                  type="button"
                                                  onClick={() => setShowModal(false)}
                                                  disabled={creating}
                                                  className="flex-1 rounded-lg border border-white/10 px-4 py-3 text-sm text-slate-300 hover:bg-white/5 disabled:opacity-50"
                                             >
                                                  Cancel
                                             </button>
                                             <button
                                                  type="submit"
                                                  disabled={
                                                       creating ||
                                                       (selectedProvider === "razorpay" && !razorpayLoaded) ||
                                                       (selectedProvider === "cashfree" && !cashfreeLoaded)
                                                  }
                                                  className="flex-1 rounded-lg bg-indigo-500 px-4 py-3 text-sm font-medium text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50"
                                             >
                                                  {creating
                                                       ? "Processing..."
                                                       : selectedProvider === "razorpay" && !razorpayLoaded
                                                            ? "Loading..."
                                                            : selectedProvider === "cashfree" && !cashfreeLoaded
                                                                 ? "Loading..."
                                                                 : "Continue to payment"}
                                             </button>
                                        </div>
                                   </form>
                              </div>
                         </div>
                    )}
               </div>
          </>
     );
}
