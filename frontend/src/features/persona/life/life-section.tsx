import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { ErrorState } from '@/components/empty-state';
import { Section } from '@/components/field';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { qk } from '@/lib/query';
import { ArcsEditor } from './arcs-editor';
import { CanonList } from './canon-list';
import { DayCard } from './day-timeline';
import { usePersonaId } from '../persona-scope';
import { lifeApi } from './life-api';
import { addDays } from './life-logic';
import { WorldEditor } from './world-editor';

const PLAN_POLL_MS = 4_000;
const PLAN_WAIT_MS = 120_000;

function Loading({ query }: { query: { isPending: boolean; isError: boolean; refetch: () => unknown } }) {
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />;
  return query.isPending ? <Skeleton className="h-32 w-full" /> : null;
}

/** Her day: today and tomorrow; after «Спланировать заново» the days are re-read for a while. */
function DaysTab() {
  const personaId = usePersonaId();
  const [pollUntil, setPollUntil] = useState(0);
  const days = useQuery({
    queryKey: qk.days(personaId),
    queryFn: () => lifeApi.days(personaId),
    refetchInterval: () => (Date.now() < pollUntil ? PLAN_POLL_MS : false),
  });
  if (!days.data) return <Loading query={days} />;
  const { today, now_minute: nowMinute, items } = days.data;
  const tomorrow = addDays(today, 1);
  const byDay = new Map(items.map((d) => [d.day, d]));
  const onPlanned = () => setPollUntil(Date.now() + PLAN_WAIT_MS);
  // Side by side only when the section itself is wide enough; a day being edited takes the whole row.
  return (
    <div className="@container">
      <div className="grid gap-3 @min-[64rem]:grid-cols-2">
        <DayCard label="Сегодня" date={today} day={byDay.get(today)} nowMinute={nowMinute} onPlanned={onPlanned} />
        <DayCard label="Завтра" date={tomorrow} day={byDay.get(tomorrow)} nowMinute={null} onPlanned={onPlanned} />
      </div>
    </div>
  );
}

/** «Жизнь»: her world, story arcs, her day and canon (docs/persona-life.md). */
export function LifeSection() {
  const personaId = usePersonaId();
  const world = useQuery({ queryKey: qk.life(personaId), queryFn: () => lifeApi.world(personaId) });
  const arcs = useQuery({ queryKey: qk.arcs(personaId), queryFn: () => lifeApi.arcs(personaId) });
  const canon = useQuery({ queryKey: qk.canon(personaId), queryFn: () => lifeApi.canon(personaId) });
  return (
    <Section
      id="life"
      title="Жизнь"
      description="Распорядок, люди, истории и её день. Из этого модель знает, где она сейчас, что было вчера и что будет завтра."
    >
      <Tabs defaultValue="day">
        <TabsList aria-label="Жизнь персонажа">
          <TabsTrigger value="day">День</TabsTrigger>
          <TabsTrigger value="world">Мир</TabsTrigger>
          <TabsTrigger value="arcs">Истории</TabsTrigger>
          <TabsTrigger value="canon">Канон</TabsTrigger>
        </TabsList>
        <TabsContent value="day" forceMount className="data-[state=inactive]:hidden">
          <DaysTab />
        </TabsContent>
        <TabsContent value="world" forceMount className="data-[state=inactive]:hidden">
          {world.data ? <WorldEditor life={world.data} /> : <Loading query={world} />}
        </TabsContent>
        <TabsContent value="arcs" forceMount className="data-[state=inactive]:hidden">
          {arcs.data ? <ArcsEditor arcs={arcs.data.items} /> : <Loading query={arcs} />}
        </TabsContent>
        <TabsContent value="canon" forceMount className="data-[state=inactive]:hidden">
          {canon.data ? <CanonList items={canon.data.items} /> : <Loading query={canon} />}
        </TabsContent>
      </Tabs>
    </Section>
  );
}
