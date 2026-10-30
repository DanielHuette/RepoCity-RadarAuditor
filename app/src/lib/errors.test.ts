import { describe, expect, it } from "vitest";
import { NoticeError, QueryError, describeError } from "./errors";

describe("describeError", () => {
  it("explains duplicate entries", () => {
    expect(describeError(new QueryError("Suchbegriff anlegen", "duplicate key", "23505"))).toBe("Diesen Eintrag gibt es schon.");
  });

  it("passes the German message of a CRM function on", () => {
    expect(describeError(new QueryError("Angebot abschicken", "Der Kunde hat keine E-Mail-Adresse.", "P0001"))).toBe("Der Kunde hat keine E-Mail-Adresse.");
  });

  it("passes a prepared German sentence on unchanged", () => {
    expect(describeError(new NoticeError("Diesen Kunden gibt es im CRM nicht mehr."))).toBe("Diesen Kunden gibt es im CRM nicht mehr.");
  });

  it("asks to sign in again on expired sessions", () => {
    expect(describeError(new QueryError("Kunden laden", "JWT expired", "PGRST301"))).toContain("Melde dich neu an");
  });

  it("treats fetch failures as missing connection", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toContain("Keine Verbindung");
  });

  it("keeps the technical message for anything else", () => {
    expect(describeError(new QueryError("Kunden laden", "boom", "XX000"))).toBe("Das hat nicht geklappt. (Kunden laden: boom)");
    expect(describeError("kaputt")).toBe("Das hat nicht geklappt.");
  });
  it("passes on the German message of a database function", () => {
    expect(describeError(new QueryError("Prüfung starten", "Name, Ort und Branche sind Pflicht.", "22023"))).toBe(
      "Name, Ort und Branche sind Pflicht.",
    );
  });

  it("passes on the German message of a CRM function", () => {
    expect(describeError(new QueryError("Angebot annehmen", "Dieses Angebot kann nicht mehr angenommen werden.", "P0001"))).toBe(
      "Dieses Angebot kann nicht mehr angenommen werden.",
    );
    expect(describeError(new QueryError("Freigeben", "Diese Änderung ist schon entschieden.", "55000"))).toBe(
      "Diese Änderung ist schon entschieden.",
    );
    expect(describeError(new QueryError("Optimierung starten", "Auftrag nicht gefunden.", "P0002"))).toBe("Auftrag nicht gefunden.");
  });

  it("explains that readers may not change anything", () => {
    expect(describeError(new QueryError("Kunde anlegen", "new row violates row-level security policy", "42501"))).toContain("Nur Lesen");
  });
});
