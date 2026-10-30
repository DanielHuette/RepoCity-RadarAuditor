import type { AuditTexts } from "../lib/texts";
import { CopyButton } from "./CopyButton";

function Posts({ posts }: { posts: readonly string[] }) {
  return (
    <section class="panel" aria-labelledby="posts">
      <h2 id="posts">Beiträge für das Google-Profil</h2>
      <ul class="suggestions">
        {posts.map((post, index) => (
          <li key={index}>
            <p>{post}</p>
            <CopyButton text={post} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function Replies({ replies }: { replies: AuditTexts["replies"] }) {
  return (
    <section class="panel" aria-labelledby="replies">
      <h2 id="replies">Antworten auf Bewertungen</h2>
      <ul class="suggestions">
        {replies.map((item, index) => (
          <li key={index}>
            <blockquote>{item.reviewExcerpt}</blockquote>
            <p>{item.reply}</p>
            <CopyButton text={item.reply} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TextSuggestions({ texts }: { texts: AuditTexts }) {
  if (texts.posts.length === 0 && texts.replies.length === 0) return null;
  return (
    <div class="grid-2">
      {texts.posts.length > 0 && <Posts posts={texts.posts} />}
      {texts.replies.length > 0 && <Replies replies={texts.replies} />}
    </div>
  );
}
