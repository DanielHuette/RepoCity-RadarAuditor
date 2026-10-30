// Counts one page load. No cookie, nothing about the reader.
(function () {
  var q = new URLSearchParams(location.search);
  try {
    fetch("https://yizvntdwdajlgcbjcves.supabase.co/rest/v1/rpc/record_hit", {
      method: "POST",
      headers: {
        apikey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlpenZudGR3ZGFqbGdjYmpjdmVzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTIxNTc4NzksImV4cCI6MjEwNzczMzg3OX0.Gpk-QXpo1CaxyOogurryFX1Nc7_cxORbGASXS9iaBJc",
        "Content-Type": "application/json",
        "Content-Profile": "marketing"
      },
      body: JSON.stringify({
        p_host: location.host,
        p_path: location.pathname,
        p_campaign: q.get("utm_campaign"),
        p_content: q.get("utm_content")
      })
    }).catch(function () {});
  } catch (e) {}
})();
