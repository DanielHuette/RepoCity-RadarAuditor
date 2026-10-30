import { PageHead } from "../components/ui";

export function NotFound() {
  return (
    <>
      <PageHead title="Seite nicht gefunden" sub="Diese Adresse gibt es in der App nicht." />
      <a class="btn" href="/">
        Zur Startseite
      </a>
    </>
  );
}
