import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, Directive, Signal, TemplateRef, contentChild, inject, input, output } from '@angular/core';
import { MAT_BOTTOM_SHEET_DATA, MatBottomSheet, MatBottomSheetRef } from '@angular/material/bottom-sheet';
import { MatBadgeModule } from '@angular/material/badge';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { injectIsPhone } from './breakpoints';

/** Маркер шаблона с фильтрами: `<ng-template simFilters>…</ng-template>`. */
@Directive({ selector: 'ng-template[simFilters]' })
export class FiltersTemplateDirective {
  readonly template = inject<TemplateRef<unknown>>(TemplateRef);
}

interface SheetData { template: TemplateRef<unknown>; activeCount: Signal<number>; reset: () => void }

/** Содержимое MatBottomSheet на телефоне: те же фильтры, что и в строке на десктопе. */
@Component({
  selector: 'sim-filter-sheet',
  imports: [NgTemplateOutlet, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="head">
      <h2>Фильтры</h2>
      <button mat-button type="button" [disabled]="data.activeCount() === 0" (click)="data.reset()">Сбросить</button>
    </div>
    <div class="sim-sheet-body"><ng-container [ngTemplateOutlet]="data.template" /></div>
    <button mat-flat-button type="button" class="done" (click)="ref.dismiss()">Готово</button>
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: var(--sim-space-3); }
    .head { display: flex; align-items: center; justify-content: space-between; }
    .done { height: 48px; }
  `,
})
export class FilterSheetComponent {
  protected readonly data = inject<SheetData>(MAT_BOTTOM_SHEET_DATA);
  protected readonly ref = inject<MatBottomSheetRef<FilterSheetComponent>>(MatBottomSheetRef);
}

/**
 * Панель фильтров. Десктоп — одна строка: поиск `[simSearch]` + шаблон фильтров.
 * Телефон — поиск на всю ширину и кнопка «Фильтры», открывающая MatBottomSheet с тем же шаблоном.
 *
 * Использование:
 *   <sim-filter-bar [activeCount]="n" (cleared)="...">
 *     <mat-form-field simSearch class="sim-search-field">…</mat-form-field>
 *     <ng-template simFilters> …селекты с классом sim-filter-field… </ng-template>
 *   </sim-filter-bar>
 */
@Component({
  selector: 'sim-filter-bar',
  imports: [NgTemplateOutlet, MatBadgeModule, MatButtonModule, MatIconModule, MatTooltipModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="bar sim-dense" [class.stack]="stackOnPhone()">
      <ng-content select="[simSearch]" />
      @if (filters()?.template; as tpl) {
        @if (isPhone() || alwaysSheet()) {
          @if (alwaysSheet()) {
            <button
              mat-icon-button
              type="button"
              class="open icon"
              matTooltip="Фильтры"
              aria-label="Фильтры"
              aria-haspopup="dialog"
              [matBadge]="activeCount()"
              [matBadgeHidden]="activeCount() === 0"
              matBadgeSize="small"
              (click)="openSheet(tpl)"
            >
              <mat-icon class="material-icons-outlined">tune</mat-icon>
            </button>
          } @else {
            <button mat-stroked-button type="button" class="open" (click)="openSheet(tpl)" aria-haspopup="dialog">
              <mat-icon class="material-icons-outlined">tune</mat-icon>
              Фильтры
              @if (activeCount() > 0) {
                <span class="badge">{{ activeCount() }}</span>
              }
            </button>
          }
        } @else {
          <ng-container [ngTemplateOutlet]="tpl" />
          @if (resettable() && activeCount() > 0) {
            <button mat-button type="button" (click)="cleared.emit()">Сбросить</button>
          }
        }
      }
      <ng-content />
    </div>
  `,
  styles: `
    :host { display: block; }
    .bar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sim-space-2); }
    /* Высота = высота поля формы в текущей плотности (токен задаёт .sim-dense) */
    .open { height: var(--mat-form-field-container-height, 56px); flex: none; }
    .open.icon { width: var(--mat-form-field-container-height, 56px); padding: 0; }
    .badge {
      min-width: 18px; height: 18px; padding: 0 5px; margin-left: 6px; border-radius: 9px; font-size: 11px; line-height: 18px;
      text-align: center; background: var(--sim-accent); color: var(--mat-sys-on-primary);
    }
    @media (max-width: 767px) {
      .bar { flex-wrap: nowrap; }
      .bar.stack { flex-wrap: wrap; }
      .bar.stack .open { flex: 1 1 100%; }
    }
  `,
})
export class FilterBarComponent {
  /** Число активных фильтров (для бейджа и кнопки «Сбросить»). */
  readonly activeCount = input(0);
  readonly resettable = input(true);
  /** Фильтры всегда в нижней панели (узкие контейнеры, напр. список диалогов). */
  readonly alwaysSheet = input(false);
  /** На телефоне поле поиска/периода занимает всю строку, кнопка «Фильтры» — под ним. */
  readonly stackOnPhone = input(false);
  readonly cleared = output<void>();

  protected readonly filters = contentChild(FiltersTemplateDirective);
  protected readonly isPhone = injectIsPhone();
  private readonly sheet = inject(MatBottomSheet);

  protected openSheet(tpl: TemplateRef<unknown>): void {
    const data: SheetData = { template: tpl, activeCount: this.activeCount, reset: () => this.cleared.emit() };
    this.sheet.open(FilterSheetComponent, { data, ariaLabel: 'Фильтры' });
  }
}
