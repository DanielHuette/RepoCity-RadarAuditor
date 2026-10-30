// The checks of a new audit, area by area: one checkbox per area and per check, areas open on demand.
import { useId, useState } from "preact/hooks";
import type { CatalogueArea, CatalogueCheck } from "../lib/catalogue";
import {
  areaState,
  areaTags,
  checkTags,
  enabledCount,
  orderedAreas,
  setAll,
  setArea,
  setCheck,
  type AreaState,
  type Tag,
} from "../lib/checkSelection";

interface CheckboxProps {
  state: AreaState;
  label?: string;
  onChange: (enabled: boolean) => void;
}

function Checkbox({ state, label, onChange }: CheckboxProps) {
  return (
    <input
      type="checkbox"
      class="cb"
      checked={state === "all"}
      indeterminate={state === "some"}
      aria-label={label}
      onChange={(event) => {
        onChange(event.currentTarget.checked);
      }}
    />
  );
}

function Tags({ tags }: { tags: readonly Tag[] }) {
  if (tags.length === 0) return null;
  return (
    <span class="tags">
      {tags.map((tag) => (
        <span key={tag.label} class={tag.isNew ? "tag tag--new" : "tag"}>
          {tag.label}
        </span>
      ))}
    </span>
  );
}

function CheckLine({ check, disabled, onChange }: { check: CatalogueCheck; disabled: readonly string[]; onChange: (next: string[]) => void }) {
  return (
    <li>
      <label class="sel-check">
        <Checkbox
          state={disabled.includes(check.id) ? "none" : "all"}
          onChange={(enabled) => {
            onChange(setCheck(disabled, check.id, enabled));
          }}
        />
        <span class="sel-check__text">
          <span class="sel-check__name">
            {check.name}
            <Tags tags={checkTags(check)} />
          </span>
          <span class="sel-check__why">{check.why}</span>
        </span>
      </label>
    </li>
  );
}

interface AreaRowProps {
  area: CatalogueArea;
  disabled: readonly string[];
  open: boolean;
  onToggle: () => void;
  onChange: (next: string[]) => void;
}

function AreaHeader({ area, disabled, open, onToggle, onChange, listId }: AreaRowProps & { listId: string }) {
  const { enabled, total } = enabledCount(disabled, [area]);
  return (
    <div class="sel-row">
      <Checkbox
        state={areaState(disabled, area)}
        label={`${area.name}: alle Prüfungen`}
        onChange={(on) => {
          onChange(setArea(disabled, area, on));
        }}
      />
      <button type="button" class="sel-row__open" aria-expanded={open} aria-controls={listId} onClick={onToggle}>
        <span class="sel-row__text">
          <span class="sel-row__name">{area.name}</span>
          <Tags tags={areaTags(area)} />
        </span>
        <span class="sel-row__count num" aria-label={`${String(enabled)} von ${String(total)} an`}>
          {enabled}/{total}
        </span>
        <span class="chevron" aria-hidden="true" />
      </button>
    </div>
  );
}

function AreaRow(props: AreaRowProps) {
  const listId = useId();
  const { area, disabled, open, onChange } = props;
  return (
    <li class="sel-area">
      <AreaHeader {...props} listId={listId} />
      {open && (
        <ul id={listId} class="sel-checks">
          {area.checks.map((check) => (
            <CheckLine key={check.id} check={check} disabled={disabled} onChange={onChange} />
          ))}
        </ul>
      )}
    </li>
  );
}

interface CheckSelectionProps {
  areas: readonly CatalogueArea[];
  disabled: readonly string[];
  onChange: (next: string[]) => void;
}

function AllChecksRow({ areas, disabled, onChange }: CheckSelectionProps) {
  const { enabled, total } = enabledCount(disabled, areas);
  const all: AreaState = enabled === total ? "all" : enabled === 0 ? "none" : "some";
  return (
    <label class="sel-row sel-row--all">
      <Checkbox
        state={all}
        onChange={(on) => {
          onChange(setAll(areas, on));
        }}
      />
      <span class="sel-row__name">Alle Prüfungen</span>
      <span class="sel-row__count num">
        {enabled} von {total}
      </span>
    </label>
  );
}

/** Ids of the areas shown open, and the toggle for one of them. */
function useOpenAreas(): [ReadonlySet<string>, (areaId: string) => void] {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (areaId: string) => {
    setOpen((previous) => {
      const next = new Set(previous);
      if (!next.delete(areaId)) next.add(areaId);
      return next;
    });
  };
  return [open, toggle];
}

export function CheckSelection({ areas, disabled, onChange }: CheckSelectionProps) {
  const [open, toggle] = useOpenAreas();
  return (
    <div class="selection" role="group" aria-label="Prüfungen auswählen">
      <AllChecksRow areas={areas} disabled={disabled} onChange={onChange} />
      <ul class="sel-areas">
        {orderedAreas(areas).map((area) => (
          <AreaRow
            key={area.id}
            area={area}
            disabled={disabled}
            open={open.has(area.id)}
            onToggle={() => {
              toggle(area.id);
            }}
            onChange={onChange}
          />
        ))}
      </ul>
    </div>
  );
}
