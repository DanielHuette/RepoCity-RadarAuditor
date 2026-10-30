const COPYABLE_FIELDS = ["code", "text"] as const;

/** What the "Kopieren" button puts on the clipboard: a ready text or code field, else the whole payload as JSON. */
export function formatFixPayload(payload: Record<string, unknown> | null): string | null {
  if (payload === null) return null;
  for (const field of COPYABLE_FIELDS) {
    const value = payload[field];
    if (typeof value === "string" && value.trim() !== "") return value;
    if (value !== null && typeof value === "object") return JSON.stringify(value, null, 2);
  }
  if (Object.keys(payload).length === 0) return null;
  return JSON.stringify(payload, null, 2);
}

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}
