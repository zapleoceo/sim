import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

@Component({
  selector: 'sim-avatar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[style.width.px]': 'size()', '[style.height.px]': 'size()', '[style.font-size.px]': 'size() * 0.4' },
  template: `
    @if (url()) {
      <img [src]="url()" [alt]="name()" />
    } @else {
      <span aria-hidden="true">{{ initials() }}</span>
    }
  `,
  styles: `
    :host {
      display: inline-flex;
      flex: none;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      overflow: hidden;
      background: var(--sim-accent-soft);
      color: var(--sim-accent);
      font-weight: 600;
      user-select: none;
    }
    img { width: 100%; height: 100%; object-fit: cover; }
  `,
})
export class AvatarComponent {
  readonly name = input.required<string>();
  readonly url = input<string | null>(null);
  readonly size = input(40);

  protected readonly initials = computed(() =>
    this.name()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? '')
      .join('') || '?',
  );
}
