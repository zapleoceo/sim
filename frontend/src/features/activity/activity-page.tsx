import { getRouteApi } from '@tanstack/react-router';
import { Page, PageHeader } from '@/components/page';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';
import { ActivityFeed } from './activity-feed';

const route = getRouteApi('/_app/activity');

export function ActivityPage() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const { isOwner } = useAuth();
  // Operators never get the LLM log, even with ?tab=llm in the URL.
  const llm = search.tab === 'llm' && isOwner;
  const channelId = search.channel_id ?? null;

  return (
    <Page>
      <PageHeader title="Активность" subtitle="Сообщения по всем каналам и журнал обращений к моделям" />
      <Tabs
        value={llm ? 'llm' : 'messages'}
        onValueChange={(v) => void navigate({ search: (s) => ({ ...s, tab: v === 'llm' ? 'llm' : undefined }) })}
      >
        <TabsList aria-label="Вид активности">
          <TabsTrigger value="messages">Сообщения</TabsTrigger>
          {isOwner && <TabsTrigger value="llm">Вызовы ИИ</TabsTrigger>}
        </TabsList>
        <TabsContent value="messages" className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            Сохранённые сообщения и их текущий статус. Метрики сообщения относятся к генератору; журнал всех вызовов ИИ
            доступен владельцу.
          </p>
          <ActivityFeed channelId={channelId} />
        </TabsContent>
        {isOwner && (
          <TabsContent value="llm" className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              Отдельные попытки обращения к модели, включая ошибки, контролёр и симуляции. Это не количество сообщений
              клиентам.
            </p>
            <ActivityFeed llm channelId={channelId} />
          </TabsContent>
        )}
      </Tabs>
    </Page>
  );
}
