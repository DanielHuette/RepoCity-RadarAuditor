Lies `skills/radar-auswerter/SKILL.md` und befolge ihn.

Ist ein Ordner angegeben (`$ARGUMENTS`), bearbeite nur diesen Ordner: erzeuge dort `findings.json` und `texte.json` nach den Regeln des Skills, ändere sonst nichts und führe weder `jobs pull` noch `jobs push` aus; beides erledigt der Sammler, der dich so aufruft. Antworte danach mit einer Zeile: Anzahl der Befunde.

Ohne Ordner: Führe `uv run radarauditor jobs pull` aus. Für jeden Ordner unter `work/analyse/<job-id>/` erzeuge `findings.json` und `texte.json` nach den Regeln des Skills. `texte.json` muss genau dem Schema im Skill folgen (Modell `ReportTexts`); `jobs push` lehnt alles andere mit der Fehlermeldung des Modells ab. Danach `uv run radarauditor jobs push`. Melde in drei Zeilen je Audit: Gesamtpunkte, die drei schwersten Befunde, was der Kunde davon hat.
