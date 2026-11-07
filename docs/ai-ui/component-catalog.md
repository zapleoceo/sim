# UI component catalog

CONFIRMED at349dc66, Angular19 standalone Material. Source base `frontend/src/app/`.

| Component | Path | Contract / reuse |
|---|---|---|
| PageHeader | shared/page-header.component.ts | required title, optional subtitle, projected actions; wraps below768px |
| FilterBar/FiltersTemplateDirective | shared/filter-bar.component.ts | activeCount, resettable, alwaysSheet, stackOnPhone, cleared; projects search + shared filter template |
| FilterSheet | same file | Material bottom sheet with reset/done, reused form controls |
| Avatar | shared/avatar.component.ts | name/url/size; client/persona identity |
| ChannelIcon | shared/channel-icon.component.ts | required ChannelKind; four kinds, accessible label |
| GuardBadge | shared/guard-badge.component.ts | GuardInfo; native details exposes violations/original text/guard model |
| StageChip | shared/stage-chip.component.ts | funnel stage presentation |
| StatusBadge | shared/status-badge.component.ts | client status, not message delivery |
| NeedsOperatorBadge | shared/needs-operator-badge.component.ts | consistent human-attention marker |
| EmptyState | shared/empty-state.component.ts | icon/title/hint; reusable no-data presentation |
| DatetimeField | shared/datetime-field.component.ts | typed date/time form control |
| ConfirmDialog/ConfirmService | shared/confirm-dialog.component.ts | shared destructive-action confirmation |
| Russian paginator | shared/paginator-intl.ts | MatPaginator labels/range text |

No standalone rich-message component existed at discovery: inbox embeds media,guard,bubble,
author and delivery rendering. A focused extraction is justified for client feed and activity.
No activity-list page existed at discovery. Do not duplicate shell/filter-sheet/identity widgets.
