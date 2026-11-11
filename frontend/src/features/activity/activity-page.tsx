import { getRouteApi } from '@tanstack/react-router';
import { Page, PageHeader } from '@/components/page';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';
import { ActivityFeed } from './activity-feed';
import { SystemLog } from './system-log';
import { TracePanel } from './trace-panel';

const route = getRouteApi('/_app/activity');

type Tab = 'log' | 'messages' | 'llm';

export function ActivityPage() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const { isOwner } = useAuth();
  // The system log and the LLM log are for owners only, whatever the URL says.
  const tab: Tab = !isOwner ? 'messages' : (search.tab ?? 'log');
  const channelId = search.channel_id ?? null;
  const openTrace = isOwner ? (search.trace ?? null) : null;

  const setTab = (v: string): void =>
    void navigate({ search: (s) => ({ ...s, tab: v === 'log' ? undefined : (v as 'messages' | 'llm'), trace: undefined }) });
  const setTrace = (trace: string | null): void => void navigate({ search: (s) => ({ ...s, trace: trace ?? undefined }) });

  return (
    <Page>
      <PageHeader
        title="Активность"
        subtitle={isOwner ? 'Журнал всех запросов системы, сообщения по каналам и вызовы моделей' : 'Сообщения по всем каналам'}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="Вид активности">
          {isOwner && <TabsTrigger value="log">Журнал</TabsTrigger>}
          <TabsTrigger value="messages">Сообщения</TabsTrigger>
          {isOwner && <TabsTrigger value="llm">Вызовы ИИ</TabsTrigger>}
        </TabsList>
        {isOwner && (
          <TabsContent value="log" className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              Каждый запрос и каждый шаг его обработки: вебхуки, входящие, задачи с попытками, вызовы моделей и
              контролёра, отправки. Строки одного запроса связаны трейсом.
            </p>
            <SystemLog channelId={channelId} onOpenTrace={setTrace} />
          </TabsContent>
        )}
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
      {isOwner && <TracePanel traceId={openTrace} onClose={() => setTrace(null)} />}
    </Page>
  );
}
