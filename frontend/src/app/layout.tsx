
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LedgerCore | Payment Infrastructure",
  description:
    "LedgerCore merchant dashboard for payments, transactions, and webhook management.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
