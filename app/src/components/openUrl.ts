// The tab opens before the signed URL is fetched; Safari blocks window.open after an await.
export async function openInNewTab(resolve: () => Promise<string | null>): Promise<boolean> {
  const tab = window.open("about:blank", "_blank");
  if (tab !== null) tab.opener = null;
  try {
    const url = await resolve();
    if (url === null) {
      tab?.close();
      return false;
    }
    if (tab === null) window.location.assign(url);
    else tab.location.href = url;
    return true;
  } catch (error) {
    tab?.close();
    throw error;
  }
}
