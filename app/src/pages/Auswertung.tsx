import { useRoute } from "preact-iso";
import { listRecentAudits } from "../api/queries";
import { RecentAudits } from "../components/RecentAudits";
import { Loaded, PageHead } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { AuditEvaluation } from "./AuditEvaluation";

function AuditChoice() {
  const recent = useAsync(listRecentAudits, []);
  return (
    <>
      <PageHead title="Auswertung" sub="Wähle eine Prüfung, um Ergebnis, Potenzial, Befunde und Rankings zu sehen." />
      <section class="panel" aria-labelledby="choice-title">
        <h2 id="choice-title">Letzte Prüfungen</h2>
        <Loaded state={recent} label="Prüfungen werden geladen …">
          {(audits) => <RecentAudits audits={audits} />}
        </Loaded>
      </section>
    </>
  );
}

export function Auswertung() {
  const { params } = useRoute();
  const auditId = params.auditId;
  return auditId === undefined || auditId === "" ? <AuditChoice /> : <AuditEvaluation id={auditId} />;
}
