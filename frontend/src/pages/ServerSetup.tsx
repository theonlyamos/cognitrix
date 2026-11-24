import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage } from '@/lib/api';
import { Button } from '@/lib/components/ui/button';
import { Input } from '@/lib/components/ui/input';
import { Select } from '@/lib/components/ui/select';
import { Field } from '@/components/form';
import { ThemeToggle } from '@/components/ThemeToggle';
import { tr } from '@/lib/i18n';

type DbType = 'sqlite' | 'postgresql' | 'mysql' | 'mongodb';

interface SetupData {
  database: { type: DbType; host: string; port: string; name: string; user: string; password_set: boolean };
  queue: { redis: boolean };
  sqlite_path: string;
}

const PORTS: Record<DbType, string> = { sqlite: '', postgresql: '5432', mysql: '3306', mongodb: '27017' };

/** First start on a server, before anyone has an account: where the database
 *  and the job queue are. Most people keep the built-in ones and continue. */
export default function ServerSetup() {
  const navigate = useNavigate();
  const [data, setData] = useState<SetupData | null>(null);
  const [dbType, setDbType] = useState<DbType>('sqlite');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('');
  const [name, setName] = useState('');
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [queue, setQueue] = useState<'builtin' | 'redis'>('builtin');
  const [brokerUrl, setBrokerUrl] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [restart, setRestart] = useState(false);

  useEffect(() => {
    api.get<SetupData>('/setup/server')
      .then(({ data: d }) => {
        setData(d);
        setDbType(d.database.type);
        setHost(d.database.host);
        setPort(d.database.port);
        setName(d.database.name);
        setUser(d.database.user);
        setQueue(d.queue.redis ? 'redis' : 'builtin');
      })
      .catch(() => navigate('/', { replace: true }));
  }, [navigate]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSaving(true);
    try {
      const { data: result } = await api.post<{ restart_needed: boolean }>('/setup/server', {
        db_type: dbType, db_host: host, db_port: port, db_name: name, db_user: user, db_password: password,
        queue, broker_url: brokerUrl,
      });
      if (result.restart_needed) setRestart(true);
      else navigate('/signup', { replace: true });
    } catch (err) {
      setError(errorMessage(err, tr('ui.serverSetup.couldNotSave')));
    } finally {
      setSaving(false);
    }
  };

  const server = dbType !== 'sqlite';

  return (
    <div className="min-h-screen flex flex-col bg-bg text-fg">
      <header className="flex min-h-14 items-center justify-between p-6">
        <span className="font-mono text-[11px] tracking-[0.16em] text-fg-dim">{tr('ui.signup.cognitrix')}</span>
        <ThemeToggle className="ml-auto" />
      </header>
      <main className="flex flex-1 justify-center px-4 pb-12 sm:px-6">
        <div className="w-full max-w-md animate-rise">
          <p className="font-mono text-[11px] tracking-[0.18em] text-accent-ink">{tr('ui.serverSetup.kicker')}</p>
          <h1 className="mt-2 text-3xl font-bold tracking-tight">{tr('ui.serverSetup.title')}</h1>
          <p className="mt-2 text-fg-dim">{tr('ui.serverSetup.subtitle')}</p>

          {restart ? (
            <div role="status" className="mt-8 rounded-lg border border-line bg-panel p-4 text-[14px] leading-relaxed">
              <p className="font-medium">{tr('ui.serverSetup.savedRestart')}</p>
              <p className="mt-2 text-fg-dim">{tr('ui.serverSetup.restartHow')}</p>
            </div>
          ) : !data ? null : (
            <form onSubmit={save} className="mt-8 space-y-6">
              <section className="space-y-4">
                <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-fg-dim">{tr('ui.serverSetup.database')}</h2>
                <Field label={tr('ui.serverSetup.databaseKind')}>
                  <Select value={dbType} onChange={(e) => { setDbType(e.target.value as DbType); setPort(''); }}>
                    <option value="sqlite">{tr('ui.serverSetup.sqlite')}</option>
                    <option value="postgresql">{tr('ui.serverSetup.postgresql')}</option>
                    <option value="mysql">{tr('ui.serverSetup.mysql')}</option>
                    <option value="mongodb">{tr('ui.serverSetup.mongodb')}</option>
                  </Select>
                </Field>
                {!server ? (
                  <p className="text-[12.5px] text-fg-dim">{tr('ui.serverSetup.sqliteHint', { path: name || data.sqlite_path })}</p>
                ) : (
                  <>
                    <div className="grid grid-cols-[1fr_96px] gap-3">
                      <Field label={tr('ui.serverSetup.host')}><Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="db.example.com" required autoComplete="off" /></Field>
                      <Field label={tr('ui.serverSetup.port')}><Input value={port} onChange={(e) => setPort(e.target.value)} placeholder={PORTS[dbType]} inputMode="numeric" /></Field>
                    </div>
                    <Field label={tr('ui.serverSetup.databaseName')}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('ui.serverSetup.cognitrix')} autoComplete="off" /></Field>
                    <Field label={tr('ui.serverSetup.user')}><Input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" /></Field>
                    <Field label={tr('ui.serverSetup.password')}>
                      <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
                        placeholder={data.database.password_set ? tr('ui.serverSetup.passwordKept') : ''} />
                    </Field>
                  </>
                )}
              </section>

              <section className="space-y-4">
                <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-fg-dim">{tr('ui.serverSetup.jobQueue')}</h2>
                <Field label={tr('ui.serverSetup.queueKind')}>
                  <Select value={queue} onChange={(e) => setQueue(e.target.value as 'builtin' | 'redis')}>
                    <option value="builtin">{tr('ui.serverSetup.builtinQueue')}</option>
                    <option value="redis">{tr('ui.serverSetup.redis')}</option>
                  </Select>
                </Field>
                {queue === 'builtin' ? (
                  <p className="text-[12.5px] text-fg-dim">{tr('ui.serverSetup.builtinQueueHint')}</p>
                ) : (
                  <Field label={tr('ui.serverSetup.redisAddress')}>
                    <Input value={brokerUrl} onChange={(e) => setBrokerUrl(e.target.value)} autoComplete="off"
                      placeholder={data.queue.redis ? tr('ui.serverSetup.redisKept') : 'redis://localhost:6379/0'} required={!data.queue.redis} />
                  </Field>
                )}
              </section>

              {error && <p role="alert" className="border-l-2 border-danger bg-danger/5 px-3 py-2 font-mono text-[12px] text-danger-ink">{error}</p>}

              <Button type="submit" disabled={saving} className="w-full">
                {saving ? tr('ui.serverSetup.checking') : <>{tr('ui.serverSetup.continue')} <span aria-hidden>→</span></>}
              </Button>
              <p className="text-[12.5px] text-fg-dim">{tr('ui.serverSetup.laterInSettings')}</p>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
