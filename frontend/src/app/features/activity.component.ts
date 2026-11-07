import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { MatTabsModule } from '@angular/material/tabs';
import { RouterLink } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { PageHeaderComponent } from '../shared/page-header.component';
import { ActivityFeedComponent } from './activity/activity-feed.component';

@Component({
  selector: 'sim-activity',
  imports: [PageHeaderComponent, MatTabsModule, RouterLink, ActivityFeedComponent],
  templateUrl: './activity.component.html',
  styleUrl: './activity.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ActivityComponent {
  readonly tab = input<string>();
  readonly channel_id = input<string>();
  protected readonly auth = inject(AuthService);
  protected readonly llm = computed(() => this.tab() === 'llm' && this.auth.isOwner());
  protected readonly channelId = computed(() => {
    const id = Number(this.channel_id());
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  });
}
