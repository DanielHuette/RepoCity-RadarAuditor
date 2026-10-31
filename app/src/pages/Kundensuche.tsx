// Kunden finden: trade and city in, the server lists every business Google shows for it, ranked by
// signs of a neglected profile, with the reason in one sentence and the lawful ways to approach it.
// Each result can go into the CRM as a prospect or get an audit; nothing is sent to the business.
import { useLocation } from "preact-iso";
import { useState } from "preact/hooks";
import { addProspectToCrm, listRecentProspectSearches, loadProspectSearch, requestProspectSearch, type ProspectView } from "../api/prospects";
import { startFieldAudit } from "../api/queries";
import type { ProspectResult, ProspectSearch } from "../api/types";
import { CrmButton } from "../components/CrmButton";
import { ServerStatusChip } from "../components/ServerStatusChip";
import { ErrorAlert, ErrorNote, Field, Loaded, PageHead } from "../components/ui";
import { useServerStatus } from "../hooks/serverStatus";
import { useAction, useAsync } from "../hooks/useAsync";
import { useLive } from "../hooks/useLive";
import { deviceStorage, loadSelection } from "../lib/checkSelection";
import { quickAuditRequest } from "../lib/fieldAudit";
import { formatDate } from "../lib/format";
import {
  CONTACT_STATUS_LABELS,
  CONTACT_STATUS_TONES,
  canAudit,
  isSearchWaiting,
  offeredContactWays,
  searchStatusText,
  validateCrmEntry,
  validateSearch,
  type CrmEntry,
} from "../lib/prospects";

function CostNote() {
  return (
    <p class="muted small">
      Eine Suche liest bis zu 60 Betriebe aus Google Maps und zählt je 20 Betriebe eine Abfrage gegen die Limits der
      Kundensuche (Einstellungen). Dieselbe Suche wird eine Weile aus dem Speicher beantwortet; wie lange, steht bei der Liste.
    </p>
  );
}

function SearchForm() {
  const { route } = useLocation();
  const [trade, setTrade] = useState("");
  const [city, setCity] = useState("");
  const [errors, setErrors] = useState<{ trade?: string; city?: string }>({});
  const action = useAction();
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    const checked = validateSearch(trade, city);
    setErrors(checked.ok ? {} : checked.errors);
    if (checked.ok) void action.run(async () => { route(`/kunden?suche=${await requestProspectSearch(checked.trade, checked.city)}`); });
  };
  return (
    <form class="panel stack" noValidate onSubmit={submit}>
      <div class="grid-2">
        <Field label="Gewerk" value={trade} onInput={setTrade} placeholder="Dachdecker" error={errors.trade} />
        <Field label="Stadt" value={city} onInput={setCity} placeholder="Münster" error={errors.city} />
      </div>
      <ErrorAlert message={action.error} />
      <div class="cluster">
        <button type="submit" class="btn btn--primary" data-write disabled={action.busy}>
          {action.busy ? "Wird gestartet …" : "Betriebe suchen"}
        </button>
      </div>
      <CostNote />
    </form>
  );
}

function RecentSearches() {
  const recent = useAsync(listRecentProspectSearches, []);
  if (recent.data === undefined || recent.data.length === 0) return null;
  return (
    <section class="panel" aria-labelledby="recent-searches">
      <h2 id="recent-searches">Letzte Suchen</h2>
      <ul class="stack stack--tight">
        {recent.data.map((search) => (
          <li key={search.id}>
            <a href={`/kunden?suche=${search.id}`}>
              {search.trade} in {search.city}
            </a>{" "}
            <span class="muted small">{formatDate(search.requested_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ContactWays({ result }: { result: ProspectResult }) {
  const ways = offeredContactWays(result);
  if (ways.length === 0) {
    return (
      <p class="muted small">
        Das Google-Profil nennt weder Anschrift noch Telefonnummer, deshalb zeigt die App keinen Kontaktweg. Steht die Anschrift im
        Profil oder auf der Webseite, gilt dafür der Brief.
      </p>
    );
  }
  return (
    <ul class="stack stack--tight" aria-label="Kontaktwege">
      {ways.map((way) => (
        <li key={way.channel} class="small">
          <span class={`badge ${CONTACT_STATUS_TONES[way.status]}`}>
            {way.label}: {CONTACT_STATUS_LABELS[way.status]}
          </span>{" "}
          {way.note}
        </li>
      ))}
    </ul>
  );
}

function Facts({ result }: { result: ProspectResult }) {
  const rating = result.rating === null ? "keine Sterne" : `${result.rating.toLocaleString("de-DE")} Sterne`;
  return (
    <p class="muted small">
      {result.review_count} Bewertungen · {rating} · {result.photo_count} Fotos
      {result.website !== null && (
        <>
          {" · "}
          <a href={result.website} target="_blank" rel="noopener noreferrer">
            Webseite
          </a>
        </>
      )}
      {result.maps_url !== null && (
        <>
          {" · "}
          <a href={result.maps_url} target="_blank" rel="noopener noreferrer">
            Google-Profil
          </a>
        </>
      )}
    </p>
  );
}

interface CrmFormProps {
  result: ProspectResult;
  city: string;
  onDone: (crmId: string) => void;
}

function CrmForm({ result, city, onDone }: CrmFormProps) {
  const [entry, setEntry] = useState<CrmEntry>({ name: "", street: "", postalCode: "", city, phone: "" });
  const [error, setError] = useState<string | null>(null);
  const action = useAction();
  const set = (key: keyof CrmEntry) => (value: string) => { setEntry({ ...entry, [key]: value }); };
  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    const checked = validateCrmEntry(entry);
    setError(checked.ok ? null : checked.error);
    if (checked.ok) void action.run(async () => { onDone(await addProspectToCrm(result.id, checked.entry)); });
  };
  return (
    <form class="stack stack--tight" noValidate onSubmit={submit}>
      <p class="muted small">
        Name, Anschrift und Telefon aus dem {result.maps_url === null ? "Google-Profil" : <a href={result.maps_url} target="_blank" rel="noopener noreferrer">Google-Profil</a>} abschreiben. Google erlaubt nicht, die Angaben der Liste dauerhaft zu speichern.
      </p>
      <Field label="Name" value={entry.name} onInput={set("name")} error={error ?? undefined} />
      <Field label="Straße" value={entry.street} onInput={set("street")} />
      <div class="grid-2">
        <Field label="PLZ" value={entry.postalCode} onInput={set("postalCode")} inputMode="numeric" />
        <Field label="Ort" value={entry.city} onInput={set("city")} />
      </div>
      <Field label="Telefon" value={entry.phone} onInput={set("phone")} type="tel" />
      <ErrorAlert message={action.error} />
      <button type="submit" class="btn btn--primary" data-write disabled={action.busy}>
        Als Interessent speichern
      </button>
    </form>
  );
}

function CrmPart({ result, city }: { result: ProspectResult; city: string }) {
  const [crmId, setCrmId] = useState(result.crm_customer_id);
  const [open, setOpen] = useState(false);
  if (crmId !== null) {
    return (
      <div class="stack stack--tight">
        <span class="badge tone-ok">Im CRM als Interessent</span>
        <CrmButton crmCustomerId={crmId} />
      </div>
    );
  }
  if (open) return <CrmForm result={result} city={city} onDone={setCrmId} />;
  return (
    <button type="button" class="btn btn--ghost" data-write onClick={() => { setOpen(true); }}>
      Ins CRM als Interessent
    </button>
  );
}

function AuditPart({ result }: { result: ProspectResult }) {
  const { route } = useLocation();
  const action = useAction();
  const blockedReason = useServerStatus()?.blockedReason ?? null;
  const website = result.website;
  const start = () =>
    void action.run(async () => {
      if (website === null) return;
      const auditId = await startFieldAudit(quickAuditRequest(website, loadSelection(deviceStorage())));
      route(`/?pruefung=${auditId}`);
    });
  return (
    <div class="stack stack--tight">
      <button type="button" class="btn btn--ghost" data-write disabled={!canAudit(result) || blockedReason !== null || action.busy} onClick={start}>
        Audit starten
      </button>
      {!canAudit(result) && <span class="muted small">Ohne Webseite startet keine Prüfung.</span>}
      {canAudit(result) && blockedReason !== null && <span class="muted small">Prüfungen gerade nicht möglich: {blockedReason}</span>}
      <ErrorAlert message={action.error} />
    </div>
  );
}

function Objected({ result }: { result: ProspectResult }) {
  return (
    <li class="panel prospect stack">
      <span class="row__title">
        {result.rank}. {result.name || "Betrieb ohne gespeicherten Namen"}
      </span>
      <p class="notice notice--bad">Hat Werbung widersprochen – nicht ansprechen.</p>
    </li>
  );
}

function ResultCard({ result, city }: { result: ProspectResult; city: string }) {
  if (result.objected) return <Objected result={result} />;
  return (
    <li class="panel prospect stack">
      <div class="split">
        <span class="row__main">
          <span class="row__title">
            {result.rank}. {result.name}
          </span>
          <span class="row__meta">{result.address}</span>
        </span>
      </div>
      <p>{result.reasons}</p>
      <Facts result={result} />
      <ContactWays result={result} />
      <div class="cluster">
        <CrmPart result={result} city={city} />
        <AuditPart result={result} />
      </div>
    </li>
  );
}

function SearchState({ search, cacheHours }: { search: ProspectSearch; cacheHours: number }) {
  const waiting = isSearchWaiting(search);
  return (
    <>
      <p class={search.status === "failed" ? "notice notice--bad" : "notice"} role="status">
        {waiting && <span class="spinner" aria-hidden="true" />}
        {searchStatusText(search, cacheHours)}
      </p>
      {waiting && <ServerStatusChip offlineNote="die Suche läuft, sobald der Server läuft." />}
    </>
  );
}

function SearchView({ view }: { view: ProspectView }) {
  const { search, results, cacheHours } = view;
  return (
    <div class="stack">
      <a class="back" href="/kunden">
        Neue Suche
      </a>
      <PageHead
        title={`${search.trade} in ${search.city}`}
        sub="Oben stehen die Betriebe mit den meisten Lücken im Google-Profil. Werbe-Mails ohne Einwilligung sind verboten; deshalb gibt es keinen Mail-Knopf."
      />
      <SearchState search={search} cacheHours={cacheHours} />
      {results.length > 0 && (
        <ol class="stack prospects">
          {results.map((result) => (
            <ResultCard key={result.id} result={result} city={search.city} />
          ))}
        </ol>
      )}
    </div>
  );
}

function LiveSearch({ id }: { id: string }) {
  const live = useLive(
    `suche-${id}`,
    () => loadProspectSearch(id),
    [
      { table: "prospect_searches", filter: `id=eq.${id}` },
      { table: "prospect_results", filter: `search_id=eq.${id}` },
    ],
    (view) => view !== null && isSearchWaiting(view.search),
  );
  return (
    <Loaded state={{ ...live, loading: live.data === undefined }} label="Suche wird geladen …">
      {(view) => (view === null ? <ErrorNote message="Diese Suche gibt es nicht mehr." /> : <SearchView view={view} />)}
    </Loaded>
  );
}

export function Kundensuche() {
  const { query } = useLocation();
  const searchId = query.suche ?? null;
  if (searchId !== null && searchId !== "") return <LiveSearch id={searchId} />;
  return (
    <div class="stack">
      <PageHead
        title="Kunden finden"
        sub="Gewerk und Stadt eingeben. Der Server sucht alle Betriebe bei Google Maps und sortiert sie nach Hinweisen auf ein vernachlässigtes Profil."
      />
      <SearchForm />
      <RecentSearches />
    </div>
  );
}
