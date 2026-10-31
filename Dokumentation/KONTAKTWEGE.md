# Contact ways of the customer search

German version: [KONTAKTWEGE.de.md](KONTAKTWEGE.de.md)

This page explains which ways of contact the customer search shows per business and which it does not. It reflects the sources as of 31 October 2026 and is no legal advice; what the sources do not settle is listed below as uncertain.

## Result
**Context:** First approach to businesses the customer search found and that never had contact with RepoCity. | **Input:** § 7 of the German Act against Unfair Competition (UWG), fact sheets of the Chambers of Industry and Commerce (IHK), decisions of the Federal Court of Justice (BGH), guidance of the German Data Protection Conference (DSK). | **Output:** Rating per contact way, implemented in `config/kundensuche.yaml`.

| Contact way | Rating in the app | What applies | Source | Certainty |
|---|---|---|---|---|
| E-mail | not allowed, no button | Advertising by e-mail only with the recipient's prior express consent, also when the recipient is a business; a single unwanted advertising mail is already unlawful; an address on a website or business card is no consent | § 7(2) no. 2 UWG; IHK Nord Westfalen | settled |
| Phone call | "only with a reason", no dial button | Towards businesses presumed consent is enough, but only with concrete circumstances showing a factual interest in the call; that the service would be useful to the business is not enough; the consent must cover the kind of advertising (a call), not only its content | § 7(2) no. 1 UWG; IHK Nord Westfalen; BGH I ZR 88/05 and I ZR 191/03 | principle settled; whether a profile with gaps is such a circumstance is uncertain (see below) |
| Letter | allowed | Advertising letters are generally lawful; an objection of the recipient and a sticker against advertising on the letterbox must be respected; the letter must not pretend to be a personal recommendation | § 7(1) UWG; IHK Nord Westfalen | settled |
| Visit in person | allowed | Sales visits are generally lawful under settled BGH case law; unlawful once it is recognisable that the visit is unwanted (for example a sign), with a hidden advertising purpose and with pressure | IHK Frankfurt am Main | settled for homes; not expressly for business premises, see below |

## Data protection
**Context:** The customer search processes names, addresses and phone numbers of businesses; for sole traders these are data of a person. | **Input:** General Data Protection Regulation (GDPR), DSK guidance on direct marketing (February 2022). | **Output:** Rules for storage and the first letter.

| Question | Result | Source | Certainty |
|---|---|---|---|
| Legal basis for advertising without consent | legitimate interest under Art. 6(1)(f) GDPR, supported by recital 47 | DSK, section 1.2 | settled |
| Relation to the UWG | where a contact way is unlawful under the UWG, there is no legitimate interest at all; hence the app offers no advertising mail and no call without a reason | DSK, section 1.4 | settled |
| Information in the first letter | when the data do not come from the business itself, it must be informed at the latest with the first advertising (origin of the data, purpose, right to object), clearly separated from the advertising | Art. 14 GDPR; DSK, section 2.2 | settled |
| Objection | must be respected at once; a suppression list is lawful | Art. 21 GDPR; DSK, sections 5.1 and 5.2 | settled |
| Data from the Google profile | the DSK mostly considers data from a legal notice (Impressum) unusable for advertising, because the notice is mandatory and not voluntary; a Google profile is created voluntarily by the business to be found | DSK, section 4.2 | uncertain: none of the sources read says whether the DSK's reasoning applies to a Google profile |

## Storage of search results and taking a business into the CRM
**Context:** What of the Places API data may stay and for how long. RepoCity has a billing address in Germany, so the EEA (European Economic Area) versions of Google's terms apply. | **Input:** Google Maps Platform EEA Service Specific Terms (as of 10 June 2026), sections A.3 and 15; Places API EEA Permitted Uses (as of 4 June 2025). | **Output:** Storage period (`public.prospect_cache_hours()`), flow of "Ins CRM als Interessent".

| Content | What Google allows | What RepoCity does | Certainty |
|---|---|---|---|
| Place identifier (`place_id`) | may be stored (A.3 "Google ID Caching") | kept permanently, also at the CRM link and in the objection list | settled |
| Coordinates | at most 30 days (15.4) | not stored | settled |
| Name, address, phone, website, review count, photos | only for the permitted uses (15.2); permitted among others is to visualize and manage Places content related to a sales team's customers or opportunities (Permitted Use 3); the terms contain no permission to cache this content and refer to the prohibition of caching in the agreement | 24 hours in the list (`public.prospect_cache_hours()`), as long as Daniel works on the list; then the database deletes all these fields itself (hourly pg_cron schedule `radar-prospect-purge`, independent of the running server); only identifier, CRM link and objection mark stay | display and management settled; that 24 hours of cache are covered is not settled; shorter is only possible if the app stops storing the list at all |
| Data in the CRM | taking the Places fields into the CRM permanently is allowed by none of the passages read | "Ins CRM als Interessent" takes no Places field: Daniel enters name, street, postal code, city and phone himself, copied from the public Google profile (link in the form); only what he enters is stored, plus the `place_id` at the link; the line in the customer file names no reasons from the list | settled for the `place_id`; what Daniel copies from the public source is not a result of the Places API |

## Cost of the customer search
**Context:** Every page of the Text Search is a paid request. | **Input:** Google Maps Platform price list (updated 28 October 2026). | **Output:** Booking through `SourceGate.record_cost`, caps in the settings.

| Request | Tier | Price per 1,000 requests, first volume tier | Booked on |
|---|---|---|---|
| Customer search, per page (`search_prospects`) | Text Search Enterprise (website, phone, opening hours, review count) | 35 US dollars | source `prospect_search`: own daily cap (default 15 pages) and monthly cap (default 10 euros) |
| Audit: identification (`search_businesses`), list with review counts (`listed_businesses`) | Text Search Enterprise | 35 US dollars | source `places`, monthly cap default 25 euros |
| Audit: competitors (`top_competitors`) | Text Search Enterprise + Atmosphere (reviews) | 40 US dollars | source `places` |
| Audit: profile (`place_details`) | Place Details Enterprise + Atmosphere | 25 US dollars | source `places` |
| Audit: identifier and ranking (`find_place`, `ranked_place_ids`) | Text Search Essentials, identifiers only | free | source `places` (daily cap only) |

The dollar amount is converted with `GOOGLE_EUR_PER_USD` (default 1). The worker does not subtract the free monthly requests per tier, so the booked sum is never below Google's bill.

## What stays uncertain
**Context:** Points the sources read do not decide. | **Input:** Sections above. | **Output:** Open questions with the cautious setting of the app.

| Question | Setting of the app until settled |
|---|---|
| Is a Google profile with gaps a concrete circumstance for presumed consent to a call? The BGH did not consider a free directory entry enough to justify a call offering a paid entry (I ZR 191/03). | Call rated "only with a reason", no dial button, the CRM deal says "no call without a concrete reason" |
| Does the case law on sales visits also cover unannounced visits to business premises during opening hours? The source read deals with the visit at a home's door. | Visit rated allowed with the note to respect a no or a sign at once |
| The BGH decisions are cited from a summary (omsels.info); the full text of I ZR 88/05 could not be retrieved. | no effect on the app; read the full text before quoting it to customers |
| The IHK Nord Westfalen fact sheet names § 7(2) no. 3 UWG for e-mail advertising, the version on gesetze-im-internet.de lists it under no. 2. The fact sheet uses the earlier numbering; the content is the same. | none |

## What the app does with it
**Context:** Implementation. | **Input:** Result above. | **Output:** Behaviour of app, database and CRM.

| Place | Behaviour |
|---|---|
| `config/kundensuche.yaml`, `contact_ways` | single source of rating and note per contact way; the server stores them with every result |
| Page "Kunden" of the app | shows letter, visit (only with a street) and call (only with a number) per result with rating and note; e-mail never appears; there is neither a mail nor a dial button |
| `public.add_prospect_to_crm` | creates the CRM customer with Daniel's entries and the deal with the next step "Erstkontakt per Brief oder Besuch; keine Werbe-Mail, kein Anruf ohne konkreten Anlass."; refuses before anything is written when the business objected to advertising, found by name and city, phone number or `place_id`; checked with Daniel's entries and with the stored entries of the result |
| `public.advertising_objections` | objection list: every objection in the CRM (`no_follow_ups`) lands there with name, city, phone number and known `place_id`s and stays when the CRM customer is deleted; names and cities are compared without legal form, additions in brackets and special characters, and it is enough that one name starts with the other (6 characters at least; "Bäckerei Korn" matches "Bäckerei Korn Inh. Peter Müller", "Münster (Westf.)" is "Münster"); an entry without city holds everywhere; in doubt the business is refused; a match is shown in the list as "Hat Werbung widersprochen – nicht ansprechen." without way of contact and without buttons |
| `public.purge_prospect_searches` | deletes the Places fields after 24 hours; runs hourly at minute 53 as the pg_cron schedule `radar-prospect-purge` and also before every search of the server |

## Sources
**Context:** Retrieved on 31 October 2026. | **Input:** Web. | **Output:** Evidence for the tables above.

| Source | Address |
|---|---|
| § 7 UWG, version on gesetze-im-internet.de | https://www.gesetze-im-internet.de/uwg_2004/__7.html |
| IHK Nord Westfalen, advertising by phone, letter or e-mail | https://www.ihk.de/nordwestfalen/recht/rechtsthemen/wettbewerbsrecht/werbung-per-telefon-telefax-oder-e-mail-3614212 |
| IHK Frankfurt am Main, sales visits | https://www.frankfurt-main.ihk.de/recht/uebersicht-alle-rechtsthemen/wettbewerbsrecht/unlauterer-wettbewerb/unzumutbare-belaestigung/vertreterbesuche-5196346 |
| Summary of BGH decisions I ZR 88/05 (20.09.2007), I ZR 191/03 (16.11.2006), I ZR 87/02 (05.02.2004), I ZR 27/08 (11.03.2010) on presumed consent | https://www.omsels.info/die-verbote-oder-was-darf-ich-nicht/7-uwg-belaestigende-werbung/2-7-abs-2-3-uwg/emutmassliche-einwilligung |
| DSK, guidance on processing personal data for direct marketing, February 2022 | https://datenschutzkonferenz-online.de/media/oh/OH-Werbung_Februar%202022_final.pdf |
| Google Maps Platform price list, updated 28 October 2026 | https://developers.google.com/maps/billing-and-pricing/pricing |
| Google Maps Platform EEA Service Specific Terms, sections A.3 and 15, as of 10 June 2026 | https://cloud.google.com/terms/maps-platform/eea/maps-service-terms |
| Places API Permitted Uses for customers with a billing address in the EEA, as of 4 June 2025 | https://cloud.google.com/terms/maps-platform/eea-places-api-permitted-uses |
| Places API adjustments for EEA customers (in force since 8 July 2025) | https://developers.google.com/maps/comms/eea/places |
