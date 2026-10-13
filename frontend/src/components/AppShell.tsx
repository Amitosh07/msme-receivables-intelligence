import { LayoutDashboard, ReceiptText, Upload, CalendarClock, LogOut, Menu, Sparkles, Users, X } from "./Icons";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";
import { useAuth } from "../features/auth";
import { ErrorBoundary } from "./ErrorBoundary";
import { ThemeToggle } from "./ThemeToggle";
const links = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/invoices", label: "Invoices", icon: ReceiptText },
  { to: "/customers", label: "Customers", icon: Users },
  { to: "/upload", label: "Upload invoices", icon: Upload },
  { to: "/historical", label: "Historical Data", icon: CalendarClock },
  { to: "/demo", label: "Guided demo", icon: Sparkles },
];
export function AppShell() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const matched = links.find((link) => link.to === location.pathname);
  const title = matched?.label || (location.pathname.startsWith("/historical") ? "Historical Data" : "Invoice detail");
  return (
    <div className="shell">
      <aside className={open ? "sidebar open" : "sidebar"}>
        <div className="brand">
          <span>R</span>
          <div>
            Receivables
            <small>{user?.business_name}</small>
          </div>
          <button className="mobile-icon" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button>
        </div>
        <nav>
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} onClick={() => setOpen(false)}>
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="account">
          <div className="avatar">{user?.full_name.slice(0, 1)}</div>
          <div>
            <strong>{user?.full_name}</strong>
            <span>{user?.email}</span>
          </div>
          <button onClick={logout} aria-label="Log out"><LogOut size={17} /></button>
        </div>
      </aside>
      <main>
        <header>
          <button className="mobile-menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></button>
          <div>
            <p className="eyebrow">{user?.business_name}</p>
            <h1>{title}</h1>
          </div>
          <div className="header-actions">
            <ThemeToggle />
            {location.pathname !== "/upload" && <Link to="/upload" className="button primary">Upload invoice</Link>}
          </div>
        </header>
        <div className="content"><ErrorBoundary><Outlet /></ErrorBoundary></div>
      </main>
    </div>
  );
}
