import type { ComponentChildren } from "preact";
import { useLocation } from "preact-iso";
import { AccountMenu } from "./AccountMenu";
import { Icon, type IconName } from "./Icon";
import { Logo } from "./Logo";

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  matches: readonly string[];
}

const NAV: readonly NavItem[] = [
  { href: "/", label: "Prüfen", icon: "check", matches: ["/"] },
  { href: "/auswertung", label: "Auswertung", icon: "chart", matches: ["/auswertung"] },
  { href: "/angebot", label: "Angebot", icon: "quote", matches: ["/angebot"] },
  { href: "/kunden", label: "Kunden", icon: "people", matches: ["/kunden"] },
];

function isActive(item: NavItem, path: string): boolean {
  return item.matches.some((prefix) => (prefix === "/" ? path === "/" : path === prefix || path.startsWith(`${prefix}/`)));
}

function NavLinks({ path }: { path: string }) {
  return (
    <>
      {NAV.map((item) => (
        <a key={item.href} href={item.href} class="navlink" aria-current={isActive(item, path) ? "page" : undefined}>
          <Icon name={item.icon} />
          <span>{item.label}</span>
        </a>
      ))}
    </>
  );
}

interface ShellProps {
  children: ComponentChildren;
  admin?: boolean | undefined;
  email: string;
}

function TopBar({ path, email }: { path: string; email: string }) {
  return (
    <header class="shell__top">
      <a href="/" class="shell__logo" aria-label="Zur Startseite">
        <Logo size={34} />
      </a>
      <div class="shell__tools">
        <a href="/einstellungen" class="iconbtn" aria-label="Einstellungen" aria-current={path === "/einstellungen" ? "page" : undefined}>
          <Icon name="settings" />
        </a>
        <AccountMenu email={email} />
      </div>
    </header>
  );
}

export function Shell({ children, admin, email }: ShellProps) {
  const { path } = useLocation();
  return (
    <div class="shell">
      <nav class="sidenav" aria-label="Hauptnavigation">
        <a href="/" class="sidenav__logo" aria-label="Zur Startseite">
          <Logo size={40} compact />
        </a>
        <div class="sidenav__links">
          <NavLinks path={path} />
        </div>
      </nav>
      <TopBar path={path} email={email} />
      <main class="shell__main" data-readonly={admin === false ? "" : undefined}>
        {admin === false && (
          <p class="notice readonly-note" role="status">
            <strong>Nur Lesen.</strong> Du kannst alles ansehen, aber nichts anlegen, ändern oder starten. Das darf nur der
            Administrator.
          </p>
        )}
        {children}
      </main>
      <nav class="bottomnav" aria-label="Hauptnavigation">
        <NavLinks path={path} />
      </nav>
    </div>
  );
}
