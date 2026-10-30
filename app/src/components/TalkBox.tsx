import { CopyButton } from "./CopyButton";

/** The conversation text of texte.json: what the operator says to the customer, ready to copy. */
export function TalkBox({ text }: { text: string }) {
  return (
    <section class="panel talk" aria-labelledby="talk-title">
      <h2 id="talk-title">Das sagen Sie dem Kunden</h2>
      <p class="talk__text">{text}</p>
      <div class="talk__copy">
        <CopyButton text={text} label="Text kopieren" main />
      </div>
    </section>
  );
}
