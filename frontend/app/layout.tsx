import "./globals.css";
import Link from "next/link";

export const metadata = { title: "Self-Healing CI/CD", description: "AI-powered CI/CD failure recovery" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <Link href="/" className="brand">⚕ Self-Healing CI/CD</Link>
          <span className="muted">Human-approved AI recovery</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
