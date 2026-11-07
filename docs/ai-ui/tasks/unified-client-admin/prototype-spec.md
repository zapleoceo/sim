# Unified client admin — block-first specification

## Source and evidence limits

The owner's Russian text is the functional prototype: consolidated client communication;
source, respondent, time, model and tokens on each message; a filterable activity log; usable
admin modules before connectors are connected. Existing Angular Material code is the visual
reference. No screenshot, Figma frame, design image or exact new-layout coordinates were supplied.
Reference crops are **UNAVAILABLE**, not exported or invented. Each block below explicitly
records that limitation. Later browser captures are implementation evidence, not an independent
pixel reference. Visual acceptance is structural, responsive, accessible and token-based.

Evidence labels: **PROJECT-DERIVED** = directly read existing source; **INFERRED** = proposed
new behavior from user request; **UNKNOWN** = requires runtime validation. Source SHA 349dc66.
Content authority: API/DB for business values; application copy for Russian labels; no invented
model, operator, account or token facts for historical rows.

Approved data authority is `backend-contract.md`: `/api/activity/messages`,
`/api/clients/{id}/messages`, `/api/activity/llm`. Where a visual row below says a filter is
conditional, use exactly the approved contract (LLM supports persona/channel/workflow/model/
provider/outcome/date/search filters). There is no queue tab. Persisted message activity is
current-state history, not one event per delivery retry. Inbox receives compact shared
author/model/token metadata plus a client-timeline link; its sending/pause/resolve logic stays.

## Shared visual contract

- PROJECT-DERIVED: `src/styles.scss` has Inter/system stack, 14px/1.5 body, h1 22px/600,
  h2 17px/600, h3 15px/600; 4/8/12/16/24/32/48 spacing tokens; 12px card radius, 8px small radius.
- PROJECT-DERIVED: `.sim-page` max-width1280px, centered, flex column, gap16px, padding24px;
  below768px padding16px. Existing client-card overrides width to880px; retaining this is valid
  if the message layout remains readable. No requirement to force an unproven wider design.
- PROJECT-DERIVED: surfaces/borders/text/status/bubbles use `--sim-*` tokens with light-dark
  variants. No new inline hex colors; channel icon colors remain a shared-component exception.
- PROJECT-DERIVED: phone breakpoint767px, navigation rail768–1199px, wide nav1200px+.
- INFERRED validation matrix: 360,390,767,768,1024,1280,1440 CSS px; long content and keyboard
  focus at least390/1280. No document-level horizontal scrolling at360px.
- New controls use Material form fields, buttons, selects, paginator and existing filter sheet.
  Metadata is selectable text, visible without hover. Color always has a text/icon equivalent.

## Block: shell-navigation

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Existing source: `shell/nav.ts`,
`shell.component.html`, `shared/breakpoints.ts`. Proposed selector `.activity-nav-item`.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Existing menu entries | Existing admin modules | Preserve order/access semantics | Remain reachable | PROJECT-DERIVED |
| Activity icon | `history` or `receipt_long` | Shared Material outlined icon | Accessible text equivalent | INFERRED |
| Activity label | «Активность» | Alongside Диалоги/Клиенты group | Navigates `/activity` | INFERRED |
| Activity selected state | Current page | Same active style as existing nav | Keyboard and pointer selection | PROJECT-DERIVED pattern |
| Mobile overflow entry | Activity destination | Existing shell overflow/navigation pattern | Do not add a second bottom navigation | INFERRED |

Desktop→mobile: reuse shell sidenav/rail/mobile behavior; do not pin an activity panel on small
screens. Access: message activity should use the same authenticated message-reading rights as
the current inbox. Operational/raw-system log exposure must not be inferred from this.

## Block: client-identity-and-workflow

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Existing source:
`features/clients/client-card.component.{html,scss,ts}`. Selectors remain existing `.head`,
`.stepper`, `.sim-form-grid`; preserve controls while adding communication below identity.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Back link | «Все клиенты» | Before identity card | `/clients`, keyboard link | PROJECT-DERIVED |
| Client avatar | DB avatar / initials fallback | Header start; existing64px avatar | Broken/missing URL handled by shared avatar | PROJECT-DERIVED |
| Client name | DB display_name | h1; wraps long unbroken name | No ellipsis-only identity | INFERRED refinement |
| Client created time | «В базе с …» | Muted line under name | Existing date formatter | PROJECT-DERIVED |
| Client status badge | Current DB status | Header end, wraps on phone | Existing status labels | PROJECT-DERIVED |
| Telegram bot link | Linked dialog when present | Icon + account/channel text | Opens exact inbox dialog | INFERRED enhancement |
| Telegram account link | Linked dialog when present | Distinct existing kind icon | Opens exact inbox dialog | INFERRED enhancement |
| WhatsApp link | Linked dialog when present | Existing icon, label | Opens exact inbox dialog | INFERRED enhancement |
| Instagram link | Linked dialog when present | Existing icon, label | Opens exact inbox dialog | INFERRED enhancement |
| First-contact stage | Existing stage button | Existing stepper | Existing server permission/validation | PROJECT-DERIVED |
| Engaged stage | Existing stage button | Existing stepper | Preserve selection | PROJECT-DERIVED |
| Needs-identified stage | Existing stage button | Existing stepper | Preserve selection | PROJECT-DERIVED |
| Interested stage | Existing stage button | Existing stepper | Preserve selection | PROJECT-DERIVED |
| Handed-to-partner stage | Existing stage button | Existing stepper | Preserve selection | PROJECT-DERIVED |
| Registered stage | Partner-only locked control | Existing lock/hint | No manual payment confirmation | PROJECT-DERIVED |
| Paid stage | Partner-only locked control | Existing lock/hint | No manual payment confirmation | PROJECT-DERIVED |
| Status selector | Active/paused/refused/no_response | Existing outlined field | Existing update/revert-on-error behavior | PROJECT-DERIVED |
| Name field | Client editing | Existing typed form | Preserve save semantics | PROJECT-DERIVED |
| Next action field | Client editing | Existing typed form | Preserve save semantics | PROJECT-DERIVED |
| Next action time | Date/time control | Existing DatetimeField | Preserve conversion | PROJECT-DERIVED |
| Needs chips | DB array | Wrap in existing needs block | Omit empty block | PROJECT-DERIVED |
| Notes field | Client editing | Full-width textarea | Long/multiline content | PROJECT-DERIVED |
| Save button | «Сохранить» | Existing save bar | Disabled pristine/saving, no double submission | PROJECT-DERIVED |
| Funnel/partner history | Existing events | Separate titled section | Do not confuse with messages | PROJECT-DERIVED |

Desktop→mobile: identity flex wraps as already implemented; form becomes natural single-column;
status full width. The communication section must not remove funnel controls or editing.
Loading,404,network error must be distinct; current endless-loading-on-error is not an acceptable
new-state pattern. Include retry and back navigation where client cannot load.

## Block: client-conversation-toolbar

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Proposed `.client-conversation` and
`.conversation-filters`; use FilterBarComponent rather than a second mobile-filter system.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Section title | «Лента общения» | h2 at card start | Labels region | INFERRED |
| Boundary explanation | «Все связанные каналы этого клиента» | Muted subtitle | Does not claim global person matching | INFERRED |
| Result count | API total | Beside title/toolbar, tabular numerals | Filtered total, not page length | INFERRED |
| Search input | «Поиск по сообщениям» | Growing search field | Debounced; reset page; clear control | INFERRED |
| Channel selector | «Канал» | Outlined select | All / actual available kinds or channel IDs | INFERRED |
| Direction selector | «Направление» | Outlined select | All / incoming / outgoing | INFERRED |
| Author selector | «Кто ответил» | Outlined select | All / client / AI / operator | INFERRED |
| Delivery selector | «Доставка» | Outlined select | All / queued / sent / failed / cancelled | INFERRED |
| Refresh button | «Обновить» | Toolbar action | Disable while loading; preserves filters/page | INFERRED |
| Reset button | «Сбросить» | Existing filter bar | Clears filters, restores first/latest page | PROJECT-DERIVED pattern |
| Mobile filter button | «Фильтры» + active count | Full/remaining width | Opens shared bottom sheet | PROJECT-DERIVED pattern |
| Mobile sheet close action | «Готово» | Existing full-width48px action | Closes only; same form state | PROJECT-DERIVED |

Desktop→mobile: search and filter opener stay visible; optional filters move to the sheet.
No sticky overlay over client save bar. Safe scope: timeline is a reading surface; each row's
«Открыть диалог» chooses the channel before replying. Do not add an ambiguous cross-channel
composer or silently broadcast replies.

## Block: message-card

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Proposed shared `sim-message-card`;
the same presentation should serve client timeline and activity rows, and inbox where compatible.
Existing inbox bubble/guard/media patterns provide token and semantics reference.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Direction marker | «Входящее» / «Исходящее» | Leading metadata, icon+text | Not color-only | INFERRED |
| Source icon | Channel kind | Existing ChannelIconComponent | Tooltip and accessible name preserved | PROJECT-DERIVED |
| Source name | Channel/account label | Alongside icon, wraps | Missing account: kind label; never secret credentials | INFERRED |
| Respondent label | Client / AI / operator | Bold metadata line | Distinguish role from actual person | INFERRED |
| Respondent name | Recorded operator/client/persona | Beside role, wraps | Unknown historical name: «Не сохранён» or role only | INFERRED |
| Created time | Recorded message timestamp | Metadata end, full date/time accessible | Label timezone/format consistently | INFERRED |
| Sent time | Receipt/delivery timestamp if returned | Secondary metadata/detail | Never equate created_at with delivered_at | INFERRED |
| Delivery badge | Queued/sent/failed/cancelled | Explicit Russian status | Failed/uncertain is not proof of non-delivery | PROJECT-DERIVED contract |
| Message text | DB text | Main body, pre-wrap; overflow-wrap:anywhere | Preserve line breaks, no HTML interpretation | INFERRED |
| Image attachment | Existing safe media URL | Body before/after text | Thumbnail, descriptive alt, lazy; open safe link | PROJECT-DERIVED pattern |
| Audio attachment | Existing safe media URL | Body, constrained width | Native controls; preload none | PROJECT-DERIVED pattern |
| Other attachment | Type label + download/open link | Body row | Missing URL remains a labeled placeholder | PROJECT-DERIVED pattern |
| Guard verdict | Existing guard badge | Beneath AI text | Expand violations/original draft/model | PROJECT-DERIVED |
| Model | Recorded model ID | Metadata/detail | «Не сохранена» for AI history without value; N/A for client | INFERRED |
| Provider | Recorded provider if available | Metadata/detail | Nullable; never derive from model text | INFERRED |
| Input tokens | tokens_in | Compact tabular metric | Zero displays0; null displays «—» | INFERRED |
| Output tokens | tokens_out | Compact tabular metric | Zero displays0; null displays «—» | INFERRED |
| Total tokens | Sum only if both values known | Optional labeled metric | Do not turn missing values into0 | INFERRED |
| Cost | Recorded message cost if available | Optional detail metric | Explicit USD label only for recorded USD field | INFERRED |
| Error / uncertainty notice | Safe API status text | Expanded detail or alert line | Do not expose raw credential-bearing exceptions | INFERRED safety |
| Message ID | Stable ID | Secondary detail | Copyable/selectable, stable tracking key | INFERRED |
| Dialog link | «Открыть диалог» | Footer action | Exact dialog ID; existing reply/pause functions retained | INFERRED |
| Client link (activity only) | DB display name | Header/footer link | Exact client card | INFERRED |

Desktop→mobile: all metadata wraps naturally, links remain reachable, body width100%; no fixed
column for token metrics, no horizontal table overflow. Do not hide source/time/author/model/tokens
behind hover-only tooltips. Secondary receipt/error/guard details may use accessible disclosure.
Colors: client/AI/operator bubble tokens if bubbles are retained, otherwise neutral card surface
with role badge. Source and delivery always have text. Long Cyrillic names/model IDs/URLs wrap.

## Block: conversation-page-and-states

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Proposed `.message-feed` and `.message-pagination`.

| Element | Role | Placement / behavior | Evidence |
|---|---|---|---|
| Chronology explanation | Latest segment, ascending reading order | Clearly label order; stable timestamp+ID tie-break | INFERRED |
| Day separator | Calendar date | Between dates, not each message | INFERRED |
| Message list | API page | All linked client dialogs, no cross-client leakage | INFERRED |
| Previous/older control | Older messages | Explicit bounded pagination; not1000-row truncation | INFERRED |
| Next/newer control | Newer messages | Disabled at latest page | INFERRED |
| Page/range label | Current range and total | Shared paginator localization | PROJECT-DERIVED pattern |
| First load indicator | «Загружаем сообщения…» | In message region, not fake rows | INFERRED |
| Refresh indicator | Request in progress | Preserve usable previous rows, mark stale/loading | INFERRED |
| Empty conversation | «Сообщений пока нет» | Shared EmptyState + explanatory hint | INFERRED |
| Empty filtered result | «Ничего не найдено» | Reset action retained | INFERRED |
| Request error | «Не удалось загрузить сообщения» | Visible alert + «Повторить» | INFERRED |
| Invalid filter/date state | Field-specific validation | Do not issue invalid request | INFERRED |
| End-of-history state | No older rows | Disable previous; no repeated append | INFERRED |

If the chosen API returns newest-first pages, reverse only the page for conversation reading;
label pagination direction explicitly. Global activity remains newest-first. Filter changes reset
page; late/stale responses cannot replace current-filter data. New arrivals do not silently move
an operator reading old history. Refresh preserves current client and filtering scope.

## Block: global-activity

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. New route `/activity`, proposed `sim-activity`.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Page title | «Активность» | PageHeader h1 | Route title consistent | INFERRED |
| Subtitle | «Входящие и исходящие сообщения по всем каналам» | Muted header subtitle | Describes actual data coverage | INFERRED |
| Refresh action | «Обновить» | Header action | Preserve filters; visible busy state | INFERRED |
| Search | «Клиент или текст сообщения» if contract supports both | Shared search field | Query semantics must match API | INFERRED |
| Persona filter | «Персонаж» | Shared select | All / real personas | INFERRED |
| Channel-kind filter | «Канал» | Shared select | Four existing kinds | INFERRED |
| Direction filter | «Направление» | Shared select | All/in/out | INFERRED |
| Author filter | «Автор» | Shared select | All/client/AI/operator | INFERRED |
| Delivery filter | «Доставка» | Shared select | Four known statuses | INFERRED |
| Date-from field | «С» | Existing date/time control or Material date range | Inclusive start, timezone semantics documented | INFERRED |
| Date-to field | «По» | Matching date control | Validate end after start; date-only inclusivity explicit | INFERRED |
| Active-filter count | Number of nondefaults | Shared filter bar | Reset count follows form | PROJECT-DERIVED pattern |
| Reset action | «Сбросить» | Filter bar/sheet | Reset page as well | PROJECT-DERIVED pattern |
| Results total | API filtered total | Above list | Not count of loaded rows | INFERRED |
| Activity entries | Shared rich message cards | Newest first | Client/dialog navigation present | INFERRED |
| Paginator | Range/page size/navigation | End of region | Shared Russian localization | PROJECT-DERIVED pattern |
| Error panel | Failed request | Alert, retry | Never show empty-success on error | INFERRED |
| Empty panel | No activity/filter matches | Shared EmptyState | Connect-channel hint only when truly no data | INFERRED |

Desktop→mobile: same data as desktop; filters in bottom sheet; cards instead of an overflowing
wide table. Date inputs and page-size control fit360px. Chronology remains newest-first and labeled.
The activity header has Material tabs «Сообщения» and «Вызовы LLM». The LLM tab is visible
only to owner/superadmin; the backend must independently enforce those rights. Message reading
uses the existing authenticated inbox access. Tab state should be reflected in navigation state
so refreshing or following a channel-filtered link is predictable.

This is a message-activity view plus an operational LLM-call view, not an immutable audit of
every administrator action, raw webhook request or queue event. Do not manufacture those events.

## Block: llm-activity

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. New owner-only `/api/activity/llm`
read surface, shown within Activity's second tab; proposed `.llm-activity`.

| Element | Role / copy | Placement / style | Behavior / states | Evidence |
|---|---|---|---|---|
| Messages tab | «Сообщения» | Material tab/nav | Authenticated users, selected by default | INFERRED |
| LLM tab | «Вызовы LLM» | Adjacent Material tab/nav | Owner/superadmin only | CONFIRMED contract direction |
| Scope note | «Попытки обращений к модели, включая ошибки» | Muted under tab | Clarifies one message may require several calls | INFERRED |
| Refresh | «Обновить» | Header/toolbar action | Preserve query and tab | INFERRED |
| Search/model filter | Recorded model or supported search | Shared outlined field | API semantics explicit; no guessed IDs | INFERRED |
| Provider filter | Provider if API supports filter | Shared select/input | Real recorded value, nullable | INFERRED |
| Outcome filter | «Результат» | Shared select | All/success/error | INFERRED |
| Workflow filter | «Назначение» | Shared select if API supports | Generator/guard/simulation etc; labels reflect recorded workflow | INFERRED |
| Date-from | «С» | Shared date/time control | Same validation/zone semantics as messages | INFERRED |
| Date-to | «По» | Shared date/time control | Same validation/zone semantics as messages | INFERRED |
| Call ID | Record identity | Row/card detail | Stable ID tracking and selectable text | INFERRED |
| Call time | Recorded created_at | Row/card start | Newest-first with stable ID tie-break | INFERRED |
| Result badge | «Успешно» / «Ошибка» | Text + status color | No raw provider exception exposed | INFERRED |
| Workflow label | Recorded workflow | Metadata | Empty/unrecognized value displayed safely | INFERRED |
| Model | Recorded model | Main metadata | Missing stays«—» | INFERRED |
| Provider | Recorded provider | Main metadata | Missing stays«—» | INFERRED |
| Input tokens | tokens_in | Numeric metric |0 and null distinct | INFERRED |
| Output tokens | tokens_out | Numeric metric |0 and null distinct | INFERRED |
| Cached tokens | cached_tokens | Numeric metric | Label «Из кеша», null not assumed0 | INFERRED |
| Cost | cost_usd | Numeric metric | Explicit USD, no implied cost on missing data | INFERRED |
| Duration | latency_ms | Numeric metric | Render ms/s with consistent formatting | INFERRED |
| Dialog/client link | Known dialog link | Row/card action | Omit for calls with no linked business dialog | INFERRED |
| Safe error summary | Generic error category/status | Detail region | Never raw request/prompt/URL/token/error body | CONFIRMED contract direction |
| Total and paginator | API total and bounded page | End of log | Same shared paginator | PROJECT-DERIVED pattern |
| Empty state | «Вызовов пока нет» | Shared EmptyState | Not an integration-readiness claim | INFERRED |
| Filtered-empty state | «Ничего не найдено» | Reset action | Preserve tab | INFERRED |
| Loading/error/retry | Standard result-region states | Alert/live status | Never stale result described as fresh | INFERRED |

Desktop→mobile: wrap metric groups or use cards; do not require horizontal scrolling to see
model,tokens,result. Mobile filters reuse bottom sheet. No client data is inferred for orphaned
or simulation calls. No sum of message metrics is presented as complete call spend: generators,
guards,retries and simulations are separate actual call records. LLM request bodies/prompts are
out of scope. The operational view does not change model choice or invoke models.

## Block: channel-readiness-navigation

Desktop crop: UNAVAILABLE. Mobile crop: UNAVAILABLE. Existing `features/channels/channels.component.ts`.

| Element | Role | Placement / behavior | Evidence |
|---|---|---|---|
| Add channel | Existing wizard | Disabled until persona exists | PROJECT-DERIVED |
| Kind icon/name | Existing channel source | Shared icon + existing text | PROJECT-DERIVED |
| Persona label | Existing binding | Real assigned persona | PROJECT-DERIVED |
| Account label | Existing source identity | Not credentials | PROJECT-DERIVED |
| Session status | Existing status badge | Active/new/expired/challenge etc, no invented health | PROJECT-DERIVED |
| Last activity | Existing known timestamp | Missing remains absent, not «работает» | PROJECT-DERIVED |
| Safe error hint | Existing sanitized mapping | Visible actionable wording | PROJECT-DERIVED |
| Connect action | Existing wizard | Preserve login/QR/challenge handling | PROJECT-DERIVED |
| Disconnect action | Existing confirmation | No new auto-disconnect | PROJECT-DERIVED |
| View activity link | «Активность канала» | Additive link with filter preselection if supported | INFERRED |
| No-persona hint | Existing prerequisite | Link to persona setup if useful | PROJECT-DERIVED pattern |
| Empty channels state | Existing EmptyState | Add channel guide | PROJECT-DERIVED |

Readiness display means stored session state, not proven delivery. Do not enable publication
gates or connect real accounts during visual validation. Avoid redesigning the wizard in this task.

## Required acceptance scenarios

1. One client with Telegram and WhatsApp messages: one chronological feed, each row retains
   channel/account/dialog provenance; a different client is absent.
2. Incoming/client, AI and two distinct operators: role/name truthful; unknown historical
   operator remains unknown instead of inheriting the current dialog operator.
3. AI model/token fields: known,zero,null all distinct; guard-model is not generator-model.
4. Queued,failed,cancelled and blocked messages visible as internal records with honest status;
   never presented as delivered. Safe unknown-outcome wording from PR12 retained.
5. Filters combine; pagination remains stable, reset works; slow old responses do not overwrite.
6. Network error is retryable; empty success is not an error; client404 offers back navigation.
7. Desktop/mobile keyboard interactions, filter sheet focus, long model/string wrapping,
   media controls, page controls and existing client edit/send/pause flows remain usable.
