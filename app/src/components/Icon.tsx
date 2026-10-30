export type IconName =
  | "home" | "people" | "document" | "more" | "export" | "settings"
  | "check" | "chart" | "quote" | "account" | "link";

const PATHS: Record<IconName, string> = {
  home: "M3.5 10.5 12 4l8.5 6.5M6 9v10.5h4.5V14h3v5.5H18V9",
  people: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.8c1.9.7 3.2 2.5 3.5 5.2",
  document: "M6 3h8l4 4v14H6V3ZM14 3v4h4M9 12h6M9 16h6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  export: "M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM12 2.5v3M12 18.5v3M4.4 4.4l2.1 2.1M17.5 17.5l2.1 2.1M2.5 12h3M18.5 12h3M4.4 19.6l2.1-2.1M17.5 6.5l2.1-2.1",
  check: "M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15ZM16 16l5 5M7.5 10.5l2 2 4-4",
  chart: "M3 20.5h18M6 17v-5M10.5 17V7M15 17v-7M19.5 17V4.5",
  quote: "M3.5 12.6V4h8.6l8.4 8.4-8.6 8.6-8.4-8.4ZM8.2 8.2h.01",
  link: "M10 14a4.2 4.2 0 0 0 6 0l3.2-3.2a4.2 4.2 0 0 0-6-6l-1.4 1.4M14 10a4.2 4.2 0 0 0-6 0l-3.2 3.2a4.2 4.2 0 0 0 6 6l1.4-1.4",
  account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5c.8-3.8 3.8-6 7.5-6s6.7 2.2 7.5 6",
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width={name === "more" ? 3 : 1.7} stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
