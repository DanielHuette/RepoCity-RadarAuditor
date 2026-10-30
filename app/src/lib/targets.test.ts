import { describe, expect, it } from "vitest";
import { formatTarget } from "./targets";

describe("formatTarget", () => {
  it.each([
    ["wordpress:page:42:meta_description", "Seite 42 – Meta-Beschreibung"],
    ["wordpress:page:42:meta_title", "Seite 42 – Meta-Titel"],
    ["wordpress:post:7:title", "Beitrag 7 – Titel"],
    ["wordpress:post:7:content", "Beitrag 7 – Inhalt"],
    ["wordpress:page:3:excerpt", "Seite 3 – Kurzbeschreibung"],
    ["wordpress:page:3:schema", "Seite 3 – Strukturierte Daten"],
    ["business_profile:description", "Unternehmensprofil – Beschreibung"],
    ["business_profile:phone", "Unternehmensprofil – Telefonnummer"],
    ["business_profile:name", "Unternehmensprofil – Name"],
    ["business_profile:website", "Unternehmensprofil – Website"],
    ["business_profile:review:AbC_12-x:reply", "Unternehmensprofil – Antwort auf eine Bewertung"],
    ["manual:Eintrag bei Gelbe Seiten ", "Eintrag bei Gelbe Seiten"],
  ])("shows %s as %s", (target, text) => {
    expect(formatTarget(target)).toBe(text);
  });

  it.each([
    "wordpress:page:42:slug",
    "wordpress:page:0:title",
    "wordpress:media:4:title",
    "business_profile:hours",
    "manual:",
    "WordPress: Startseite, Seitentitel",
    "",
  ])("keeps %j as it is when it is outside the grammar", (target) => {
    expect(formatTarget(target)).toBe(target);
  });
});
